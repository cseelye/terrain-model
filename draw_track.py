#!/usr/bin/env python3.8
"""Draw GPX tracks on top of a geospatial image"""

from pyapputil.appframework import PythonApp
from pyapputil.argutil import ArgumentParser
from pyapputil.typeutil import ValidateAndDefault, StrType, PositiveNonZeroIntegerType, PositiveIntegerType
from pyapputil.logutil import GetLogger, logargs
import cv2
from osgeo import gdal, osr
import numpy as np
from affine import Affine

from geo import GPXFile, GDAL_ERROR
from util import Color

@logargs
@ValidateAndDefault({
    # "arg_name" : (arg_type, arg_default)
    "gpx_file" : (StrType(), None),
    "track_color" : (Color(), None),
    "track_width" : (PositiveNonZeroIntegerType(), 10),
    "max_width" : (PositiveIntegerType(), 0),
    "max_height" : (PositiveIntegerType(), 0),
    "input_file" : (StrType(), None),
    "output_file" : (StrType(), None),
})
def main(gpx_file,
         track_color,
         track_width,
         max_width,
         max_height,
         input_file,
         output_file):
    """
    Combine an orthoimage and a GPX file into a PNG with the image as the background
    and the tracks from the GPX file drawn over it.

    Args:
        gpx_file:       (str)                 GPX format file containing one or more tracks to draw
        track_color:    (str or tuple of int) The color to draw the tracks in, either by name or as RGB tuple
        track_width:    (int)                 Width of the track, in pixels
        input_file:     (str)                 Geospatial image to draw the tracks on
        output_file:    (str)                 Output file to create
    """

    log = GetLogger()

    if not output_file.endswith("png"):
        output_file += ".png"

    # Reproject our GPS coordinates into the spatial system of our source raster
    log.info("Reprojecting coordinates")
    src_ds = gdal.Open(input_file)
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

    # Draw the track
    log.info("Drawing track on image")
    img = cv2.imread(input_file)
    gpx = GPXFile(gpx_file)
    for track in gpx.GetTrackPoints():

        # Transform operators
        fwd_trans = Affine.from_gdal(*gt)
        rev_trans = ~fwd_trans

        # Map GPS coordinates into the coordinate system of the geo image (discarding the z coord)
        image_points = [(x,y) for x,y,_ in [gps_to_image.TransformPoint(long, lat) for lat, long in track]] # Note the swap in lat/long
        # log.debug("gps_points[0] = ({})".format(track[0]))
        # log.debug("image_points[0] = ({})".format(image_points[0]))

        # Map image coordinates into pixel coordinates using affine transform
        pixel_points = np.array([[rev_trans * (x,y) for x,y in image_points] ], np.int32)
        # log.debug("pixel_points[0] = ({})".format(pixel_points[0][0]))

        # Reshape the array into what cv2.polylines wants
        pixel_points = pixel_points.reshape((-1, 1, 2))

        cv2.polylines(img, [pixel_points], False, track_color.AsBGR(), track_width, cv2.LINE_AA)

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

    cv2.imwrite(output_file, img)
    log.passed(f"Successfully wrote {output_file}")


if __name__ == '__main__':
    parser = ArgumentParser(description="Combine an orthoimage and GPX file into a PNG with the image as background and the tracks drawn over it")
    parser.add_argument("-g", "--gpx-file", type=StrType(), metavar="FILENAME", help="GPX file to use")
    parser.add_argument("-c", "--track-color", type=Color(), metavar="COLOR", help="The color to draw the track in, either a name or RGB tuple")
    parser.add_argument("-r", "--track-width", type=PositiveNonZeroIntegerType(), default=20, metavar="PIXELS", help="The width of the track to draw, in pixels")
    parser.add_argument("-x", "--max-width", type=PositiveIntegerType(), default=0, metavar="PIXELS", help="Resize to a maximum width, in pixels")
    parser.add_argument("-y", "--max-height", type=PositiveIntegerType(), default=0, metavar="PIXELS", help="Resize to a maximum height, in pixels")
    parser.add_argument("-i", "--input-file", type=StrType(), metavar="FILENAME", help="Input file, in a raster format that GDAL can read")
    parser.add_argument("-o", "--output-file", type=StrType(), metavar="FILENAME", help="Output file (PNG format)")

# TODO For non-georeferenced images
#    parser.add_argument("-t", "--top", type=float, help="the longitude of the top row of the image")
#    parser.add_argument("-l", "--left", type=float, help="the latitude of the left most column of the image")

    args = parser.parse_args_to_dict()

    app = PythonApp(main, args)
    app.Run(**args)
