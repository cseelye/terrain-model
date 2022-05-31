#!/usr/bin/env python3
"""Crop, convert, and draw a track onto an orthoimage"""

from pathlib import Path
import tempfile

from affine import Affine
import cv2
from pyapputil.appframework import PythonApp
from pyapputil.argutil import ArgumentParser
from pyapputil.typeutil import ValidateAndDefault, OptionalValueType, StrType, BoolType, ItemList, PositiveNonZeroIntegerType, PositiveIntegerType
from pyapputil.logutil import GetLogger, logargs
from pyapputil.exceptutil import InvalidArgumentError, ApplicationError
from pyapputil.shellutil import Shell
from osgeo import gdal, osr
import numpy as np

from geo import GPXFile, get_raster_boundaries_gps, convert_and_crop_raster, GDAL_ERROR, get_cropped_image_filename
from util import Color

@logargs
@ValidateAndDefault({
    # "arg_name" : (arg_type, arg_default)
    "gpx_file" : (OptionalValueType(StrType()), None),
    "padding" : (float, 0),
    "square" :  (BoolType(), False),
    "min_lat" : (OptionalValueType(float), None),
    "min_long" : (OptionalValueType(float), None),
    "max_lat" : (OptionalValueType(float), None),
    "max_long" : (OptionalValueType(float), None),
    "draw_track": (BoolType(), False),
    "track_color" : (Color(), Color()("red")),
    "track_width" : (PositiveNonZeroIntegerType(), 10),
    "max_width" : (PositiveIntegerType(), 2048),
    "max_height" : (PositiveIntegerType(), 2048),
    "input_files" : (OptionalValueType(ItemList(StrType())), None),
    "output_file" : (StrType(), None),
    "cache_dir" : (StrType(), "cache"),
})
def main(gpx_file,
         padding,
         square,
         min_lat,
         min_long,
         max_lat,
         max_long,
         draw_track,
         track_color,
         track_width,
         max_width,
         max_height,
         input_files,
         output_file,
         cache_dir):
    """
    Crop, convert, and draw a track onto an orthoimage so it is ready to use as a UV-mapped texture on a 3D model

    Args:
        gpx_file:       (str)           A file containing one of more tracks to draw, and use to determine the area of image to crop
        padding:        (float)         Padding to add around the GPX track, in miles
        square:         (bool)          Make the region around the GPX track a square
        min_lat         (float)         Southern boundary of the region to crop
        min_long        (float)         Eastern boundary of the region to crop
        max_lat         (float)         Northern boundary of the region to crop
        max_long        (float)         Western boundary of the region to crop
        draw_track      (bool)          Whether or not to draw the track on the image
        track_color:    (str or
                        tuple of int)   The color to draw the tracks in, either by name or as RGB tuple
        track_width:    (int)           Width of the track, in pixels
        max_width:      (int)           Max width of the final image, in pixels. The image will be resized to fit inside this
        max_height:     (int)           Max height of the final image, in pixels. The image will be resized to fit inside this
        input_files:    (list of str)   List of geospatial images as input, any format that GDAL can read. If not specified then the
                                        cache will be searched for image files that cover the requested area
        output_file:    (str)           Output file to create, in PNG format
    """
    log = GetLogger()

    if not output_file.endswith("png"):
        output_file += ".png"

    if draw_track and not gpx_file:
        log.error("Missing GPX file")
        return False

    # Determine the bounds of the output
    if gpx_file and None in (min_lat, min_long, max_lat, max_long):
        log.info("Parsing GPX file")
        gpx = GPXFile(gpx_file)
        min_lat, min_long, max_lat, max_long = gpx.GetBounds(padding, square)
    if None in (min_lat, min_long, max_lat, max_long):
        raise InvalidArgumentError("You must specify an area to crop")
    log.debug(f"Requested crop boundaries top(max_lat)={max_lat} left(min_long)={min_long} bottom(min_lat)={min_lat} right(max_long)={max_long}")

    cache_dir = Path(cache_dir)
    if not input_files:
        cache_file = cache_dir / get_cropped_image_filename(max_lat, min_long, min_lat, max_long)
        if not cache_file.exists():
            log.error("Could not find image data in cache")
            return False
        input_files = [cache_file]

    # Build a virtual data set if there is more than one input file
    if len(input_files) > 1:
        log.info("Merging input files into virtual data set")
        _, input_file = tempfile.mkstemp()
        # The python interface to this is horrifying, so use the command line app
        retcode, _, stderr = Shell("gdalbuildvrt {} {}".format(input_file, " ".join(input_files)))
        if retcode != 0 or "ERROR" in stderr:
            raise ApplicationError(f"Could not merge input files: {stderr}")
    else:
        input_file = input_files[0]


    # Open the file
    ds = gdal.Open(str(input_file))
    GDAL_ERROR.check("Error parsing input file", ds)

    # Calculate the extent from the input file
    source_min_lat, source_min_long, source_max_lat, source_max_long = get_raster_boundaries_gps(ds)
    log.debug(f"Source boundaries top(max_lat)={source_max_lat} left(min_long)={source_min_long} bottom(min_lat)={source_min_lat} right(max_long)={source_max_long}")

    # Adjust output crop as necessary to fit the source image
    adjust = False
    if min_lat < source_min_lat:
        min_lat = source_min_lat
        adjust = True
    if max_lat > source_max_lat:
        max_lat = source_max_lat
        adjust = True
    if min_long < source_min_long:
        min_long = source_min_long
        adjust = True
    if max_long > source_max_long:
        max_long = source_max_long
        adjust = True
    if adjust:
        log.info("Output boundary is outside of input boundary")
        log.info(f"New crop boundaries top(max_lat)={max_lat} left(min_long)={min_long} bottom(min_lat)={min_lat} right(max_long)={max_long}")

    # Convert the image and crop to geo boundaries, save to intermediate file
    _, intermediate_file = tempfile.mkstemp()
    convert_and_crop_raster(input_file, intermediate_file, min_lat, min_long, max_lat, max_long, output_type="GTiff", remove_alpha=True)

    # Open the intermediate file and determine the projections/transform from GPS coords into the image
    src_ds = gdal.Open(str(intermediate_file))
    GDAL_ERROR.check("Error parsing input file")

    log.debug("Image xsize=%s, ysize=%s", src_ds.RasterXSize, src_ds.RasterYSize)
    wgs84 = osr.SpatialReference()
    wgs84.SetFromUserInput('WGS84')
    wgs84.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER) # Use "traditional" X=longitude, Y=latitude
    src_sr = osr.SpatialReference()
    src_sr.ImportFromWkt(src_ds.GetProjection())
    src_sr.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    gps_to_image = osr.CoordinateTransformation(wgs84, src_sr)
    gt = src_ds.GetGeoTransform()
    fwd_trans = Affine.from_gdal(*gt)
    rev_trans = ~fwd_trans

    # Load the image into cv2
    img = cv2.imread(str(input_file))

    # Draw the tracks
    if draw_track:
        log.info("Drawing track on image")
        gpx = GPXFile(gpx_file)
        for track in gpx.GetTrackPoints():

            # Map GPS coordinates into the coordinate system of the geo image (discarding the z coord)
            image_points = [(x,y) for x,y,_ in [gps_to_image.TransformPoint(long, lat) for lat, long in track]] # Note the swap in lat/long
            # log.debug("gps_points[0] = ({})".format(track[0]))
            # log.debug("image_points[0] = ({})".format(image_points[0]))

            # Map image coordinates into pixel coordinates using affine transform
            pixel_points = np.array([[rev_trans * (x,y) for x,y in image_points] ], np.int32)
            # log.debug("pixel_points[0] = ({})".format(pixel_points[0][0]))

            # Reshape the array into what cv2.polylines wants
            pixel_points = pixel_points.reshape((-1, 1, 2))

            # Draw the track on the image
            cv2.polylines(img, [pixel_points], False, track_color.as_bgr(), track_width, cv2.LINE_AA)

    # Resize the image if needed
    (height, width) = img.shape[:2]
    new_height = height
    new_width = width
    if max_width > 0 and new_width > max_width:
        ratio = float(max_width) / float(new_width)
        new_width = max_width
        new_height = int(new_height * ratio)
    if max_height > 0 and new_height > max_height:
        ratio = float(new_height) / float(new_height)
        new_height = max_height
        new_width = int(new_width * ratio)

    if new_width != width or new_height != height:
        log.info(f"Resizing image to ({new_width}, {new_height})")
        img = cv2.resize(img, (new_width, new_height), interpolation=cv2.INTER_CUBIC)

    # Write the target image
    cv2.imwrite(str(output_file), img)

    log.passed(f"Successfully wrote {output_file}")
    return True


if __name__ == '__main__':
    parser = ArgumentParser(description="Crop a spatial image to the given lat/long coordinates and convert it to a GeoTiff")
    area_group = parser.add_argument_group("Area specification", "The area to crop can be specified either with a GPX track or by absolute lat/long coordinates.")
    area_group.add_argument("-g", "--gpx-file", type=StrType(), metavar="FILENAME", help="GPX file to use")
    area_group.add_argument("-p", "--padding", type=float, metavar="MILES", help="Padding to add around the GPX track, in miles")
    area_group.add_argument("-q", "--square", action="store_true", help="Make the region around the GPX track a square")
    area_group.add_argument("-n", "--north", type=float, dest="max_lat", metavar="DEGREES", help="The northern edge of the model, in decimal degrees latitude")
    area_group.add_argument("-s", "--south", type=float, dest="min_lat", metavar="DEGREES", help="The southern edge of the model, in decimal degrees latitude")
    area_group.add_argument("-e", "--east", type=float, dest="max_long", metavar="DEGREES", help="The eastern edge of the model, in decimal degrees longitude")
    area_group.add_argument("-w", "--west", type=float, dest="min_long", metavar="DEGREES", help="The western edge of the model, in decimal degrees longitude")
    parser.add_argument("-a", "--draw-track", action="store_true", help="Draw the track(s) from the GPX file onto the image")
    parser.add_argument("-t", "--track-color", type=Color(), metavar="COLOR", help="The color to draw the track in, either a name or RGB tuple")
    parser.add_argument("-r", "--track-width", type=PositiveNonZeroIntegerType(), default=20, metavar="PIXELS", help="The width of the track to draw, in pixels")
    parser.add_argument("-x", "--max-width", type=PositiveIntegerType(), default=2048, metavar="PIXELS", help="Resize to a maximum width, in pixels")
    parser.add_argument("-y", "--max-height", type=PositiveIntegerType(), default=2048, metavar="PIXELS", help="Resize to a maximum height, in pixels")
    parser.add_argument("-i", "--input-file", dest="input_files", type=StrType(), action="append", metavar="FILENAME", help="One or more input files, in a raster format that GDAL can read. If these are not specified, the script will look for image files in the cache")
    parser.add_argument("-o", "--output-file", type=StrType(), metavar="FILENAME", help="Output file (PNG format)")
    parser.add_argument("-c", "--cache-dir", type=StrType(), default="cache", metavar="DIRNAME", help="Directory to look for image files in, if input-file was not specified")
    args = parser.parse_args_to_dict()

    app = PythonApp(main, args)
    app.Run(**args)
