#!/usr/bin/env python2.7

from pyapputil.appframework import PythonApp
from pyapputil.argutil import ArgumentParser, GetFirstLine
from pyapputil.typeutil import ValidateAndDefault, OptionalValueType, StrType, BoolType
from pyapputil.logutil import GetLogger, logargs
from pyapputil.exceptutil import InvalidArgumentError
from geo import GPXFile, get_raster_boundaries_gps, convert_and_crop_raster, degree_lat_to_miles, degree_long_to_miles, get_raster_boundaries_geo
from osgeo import gdal, ogr, osr


@logargs
@ValidateAndDefault({
    # "arg_name" : (arg_type, arg_default)
    "gpx_file" : (OptionalValueType(StrType), None),
    "padding" : (float, 0),
    "square" :  (BoolType, False),
    "min_lat" : (OptionalValueType(float), None),
    "min_long" : (OptionalValueType(float), None),
    "max_lat" : (OptionalValueType(float), None),
    "max_long" : (OptionalValueType(float), None),
    "input_file" : (StrType, None),
    "output_file" : (StrType, None),
})
def main(gpx_file,
         padding,
         square,
         min_lat,
         min_long,
         max_lat,
         max_long,
         input_file,
         output_file):

    log = GetLogger()

    # Determine the bounds of the output
    if gpx_file:
        log.info("Parsing GPX file")
        gpx = GPXFile(gpx_file)
        min_lat, min_long, max_lat, max_long = gpx.GetBounds(padding, square)
    if None in (min_lat, min_long, max_lat, max_long):
        raise InvalidArgumentError("You must specify an area to crop")
    log.debug("Crop boundaries = TL({}, {}) BR({}, {})".format(min_long, max_lat, max_long, min_lat))

    # Open the file
    ds = gdal.Open(input_file)

    # Calculate the extent from the input file
    source_min_lat, source_min_long, source_max_lat, source_max_long = get_raster_boundaries_gps(ds)
    log.debug("Source boundaries = TL({}, {}) BR({}, {})".format(source_min_long, source_max_lat, source_max_long, source_min_lat))

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
        log.info("Output boundary is outside of input boundary; adjusted output boundaries = TL({}, {}) BR({}, {})".format(min_long, max_lat, max_long, min_lat))

    # Crop and convert the image
    convert_and_crop_raster(input_file, output_file, min_lat, min_long, max_lat, max_long, output_type="PNG", remove_alpha=True)
    log.passed("Successfully created {}".format(output_file))


if __name__ == '__main__':
    parser = ArgumentParser(description="Crop a geo raster image to the given lat/long coordinates and convert it to a PNG")
    area_group = parser.add_argument_group("Area specification", "The area to crop can be specified either with a GPX track or by absolute lat/long coordinates.")
    area_group.add_argument("-g", "--gpx-file", type=StrType, metavar="FILENAME", help="GPX file to use")
    area_group.add_argument("-p", "--padding", type=float, metavar="MILES", help="Padding to add around the GPX track, in miles")
    area_group.add_argument("-q", "--square", type=float, metavar="MILES", help="Make the region around the GPX track a square")
    area_group.add_argument("-n", "--north", type=float, dest="max_lat", metavar="DEGREES", help="The northern edge of the model, in decimal degrees latitude")
    area_group.add_argument("-s", "--south", type=float, dest="min_lat", metavar="DEGREES", help="The southern edge of the model, in decimal degrees latitude")
    area_group.add_argument("-e", "--east", type=float, dest="max_long", metavar="DEGREES", help="The eastern edge of the model, in decimal degrees longitude")
    area_group.add_argument("-w", "--west", type=float, dest="min_long", metavar="DEGREES", help="The western edge of the model, in decimal degrees longitude")
    parser.add_argument("-i", "--input-file", type=StrType, metavar="FILENAME", help="Input file, in a raster format that GDAL can read")
    parser.add_argument("-o", "--output-file", type=StrType, metavar="FILENAME", help="Output file (PNG format)")
    args = parser.parse_args_to_dict()

    app = PythonApp(main, args)
    app.Run(**args)
