#!/usr/bin/env python3.8
"""Crop a spatial image to given coordinates and convert it to a GeoTIFF"""

from pyapputil.appframework import PythonApp
from pyapputil.argutil import ArgumentParser
from pyapputil.typeutil import ValidateAndDefault, OptionalValueType, StrType, BoolType, ItemList
from pyapputil.logutil import GetLogger, logargs
from pyapputil.exceptutil import InvalidArgumentError, ApplicationError
from pyapputil.shellutil import Shell
from osgeo import gdal
import tempfile
from geo import GPXFile, get_raster_boundaries_gps, convert_and_crop_raster, GDAL_ERROR


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
    "input_files" : (ItemList(StrType()), None),
    "output_file" : (StrType(), None),
})
def main(gpx_file,
         padding,
         square,
         min_lat,
         min_long,
         max_lat,
         max_long,
         input_files,
         output_file):
    """
    Crop a geospatial image to the given coordinates and convert it to a GeoTIFF

    Args:
        gpx_file:       (str)           A file containing one of more tracks to use to determine the area of terrain to crop
        padding:        (float)         Padding to add around the GPX track, in miles
        square:         (bool)          Make the region around the GPX track a square
        min_lat         (float)         Southern boundary of the region to crop
        min_long        (float)         Eastern boundary of the region to crop
        max_lat         (float)         Northern boundary of the region to crop
        max_long        (float)         Western boundary of the region to crop
        input_files:    (list of str)   List of geospatial images to crop, any format that GDAL can read
        output_file:    (str)           Output file to create, in GeoTIFF format
    """

    log = GetLogger()

    # Build a virtual data set if there is more than one input file
    if len(input_files) > 1:
        log.info("Merging input files into virtual data set")
        _, input_file = tempfile.mkstemp()
        # The python interface to this is horrifying, so use the command line app
        retcode, _, stderr = Shell("gdalbuildvrt {} {}".format(input_file, " ".join(input_files)))
        if retcode != 0 or "ERROR" in stderr:
            raise ApplicationError("Could not merge input files: {}".format(stderr))
    else:
        input_file = input_files[0]

    if not output_file.endswith(".tif") and not output_file.endswith(".tiff"):
        output_file += ".tif"

    # Determine the bounds of the output
    if gpx_file:
        log.info("Parsing GPX file")
        gpx = GPXFile(gpx_file)
        min_lat, min_long, max_lat, max_long = gpx.GetBounds(padding, square)
    if None in (min_lat, min_long, max_lat, max_long):
        raise InvalidArgumentError("You must specify an area to crop")
    log.debug("Requested crop boundaries top(max_lat)={} left(min_long)={} bottom(min_lat)={} right(max_long)={}".format(max_lat, min_long, min_lat, max_long))

    # Open the file
    ds = gdal.Open(input_file)
    GDAL_ERROR.check("Error parsing input file", ds)

    # Calculate the extent from the input file
    source_min_lat, source_min_long, source_max_lat, source_max_long = get_raster_boundaries_gps(ds)
    log.debug("Source boundaries top(max_lat)={} left(min_long)={} bottom(min_lat)={} right(max_long)={}".format(source_max_lat, source_min_long, source_min_lat, source_max_long))

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
        log.info("New crop boundaries top(max_lat)={} left(min_long)={} bottom(min_lat)={} right(max_long)={}".format(max_lat, min_long, min_lat, max_long))

    # Crop and convert the image
    convert_and_crop_raster(input_file, output_file, min_lat, min_long, max_lat, max_long, output_type="GTiff", remove_alpha=True)
    log.passed("Successfully created {}".format(output_file))
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
    parser.add_argument("-i", "--input-file", dest="input_files", type=StrType(), action="append", metavar="FILENAME", help="One or more input files, in a raster format that GDAL can read")
    parser.add_argument("-o", "--output-file", type=StrType(), metavar="FILENAME", help="Output file (GeoTiff format)")
    args = parser.parse_args_to_dict()

    app = PythonApp(main, args)
    app.Run(**args)
