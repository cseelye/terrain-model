#!/usr/bin/env python3.8
"""Download 3D elevation data from USGS"""

from pyapputil.appframework import PythonApp
from pyapputil.argutil import ArgumentParser
from pyapputil.typeutil import ValidateAndDefault, OptionalValueType, StrType, BoolType
from pyapputil.logutil import GetLogger, logargs
from pyapputil.exceptutil import InvalidArgumentError
from geo import GPXFile, get_dem_data, get_cropped_elevation_filename
from pathlib import Path

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
    "cache_dir" : (StrType(), "cache"),
})
def download_elevation_data(gpx_file,
                            padding,
                            square,
                            min_lat,
                            min_long,
                            max_lat,
                            max_long,
                            cache_dir):
    """
    Download 3D elevation data for a region

    Args:
        gpx_file:       (str)   A file containing one of more tracks to use to determine the area of terrain to model
        padding:        (float) Padding to add around the GPX track, in miles
        min_lat         (float) Southern boundary of the region to model
        min_long        (float) Eastern boundary of the region to model
        max_lat         (float) Northern boundary of the region to model
        max_long        (float) Western boundary of the region to model
        cache_dir       (str)   Directory to download the files to
    """
    log = GetLogger()

    # Determine the bounds of the output
    if gpx_file:
        log.info("Parsing GPX file")
        gpx = GPXFile(gpx_file)
        min_lat, min_long, max_lat, max_long = gpx.GetBounds(padding, square)

    if None in (min_lat, min_long, max_lat, max_long):
        raise InvalidArgumentError("You must specify an area to crop")

    log.info("Model boundaries top(max_lat)={} left(min_long)={} bottom(min_lat)={} right(max_long)={}".format(
        max_lat, min_long, min_lat, max_long))

    # Get the elevation data
    cache_dir = Path(cache_dir)
    dem_filename = Path(get_cropped_elevation_filename(max_lat, min_long, min_lat, max_long))
    get_dem_data(dem_filename, min_lat, min_long, max_lat, max_long, cache_dir)


if __name__ == '__main__':
    parser = ArgumentParser(description="Download 3D elevation data for a region")
    area_group = parser.add_argument_group("Area specification", "The area covered by the data can be specified either with a GPX track or by absolute lat/long coordinates")
    area_group.add_argument("-g", "--gpx-file", type=StrType(), metavar="FILENAME", help="GPX file to use")
    area_group.add_argument("-p", "--padding", type=float, metavar="MILES", help="Padding to add around the GPX track, in miles")
    area_group.add_argument("-q", "--square", action="store_true", help="Make the region around the GPX track a square")
    area_group.add_argument("-n", "--north", type=float, dest="max_lat", metavar="DEGREES", help="The northern edge of the model, in decimal degrees latitude")
    area_group.add_argument("-s", "--south", type=float, dest="min_lat", metavar="DEGREES", help="The southern edge of the model, in decimal degrees latitude")
    area_group.add_argument("-e", "--east", type=float, dest="max_long", metavar="DEGREES", help="The eastern edge of the model, in decimal degrees longitude")
    area_group.add_argument("-w", "--west", type=float, dest="min_long", metavar="DEGREES", help="The western edge of the model, in decimal degrees longitude")
    parser.add_argument("-c", "--cache-dir", type=StrType(), default="cache", metavar="DIRNAME", help="Directory to keep downloaded data/working files in. Reusing these files can speed up multiple runs against the same input")
    args = parser.parse_args_to_dict()

    app = PythonApp(download_elevation_data, args)
    app.Run(**args)
