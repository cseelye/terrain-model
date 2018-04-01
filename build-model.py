#!/usr/bin/env python2.7

from pyapputil.appframework import PythonApp
from pyapputil.argutil import ArgumentParser, GetFirstLine
from pyapputil.typeutil import ValidateAndDefault, OptionalValueType, StrType, BoolType
from pyapputil.logutil import GetLogger, logargs
from pyapputil.exceptutil import ApplicationError, InvalidArgumentError
from util import HTTPDownloader
from geo import GPXFile, convert_and_crop_raster, dem_to_model, degree_long_to_miles, degree_lat_to_miles
import math
import os
import tempfile
import zipfile

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
    "model_file" : (OptionalValueType(StrType), None),
    "z_exaggeration" : (float, 1.0),
})
def build_model(gpx_file,
                padding,
                square,
                min_lat,
                min_long,
                max_lat,
                max_long,
                model_file,
                z_exaggeration):
    """
    Create a 3D model of terrain

    Args:
        gpx_file:       A file containing one of more tracks to use to determine the area of terrain to model (string)
        padding:        Padding to add around the GPX track, in miles (float)
        min_lat         Southern boundary of the region to model (float)
        min_long        Eastern boundary of the region to model (float)
        max_lat         Northern boundary of the region to model (float)
        max_long        Western boundary of the region to model (float)
        model_file:     File name to write the 3D model to (str)
        z_exaggeration: How much Z-axis exaggeration to apply to the model (float)
    """
    log = GetLogger()

    # Determine the bounds of the output
    if gpx_file:
        log.info("Parsing GPX file")
        gpx = GPXFile(gpx_file)
        min_lat, min_long, max_lat, max_long = gpx.GetBounds(padding, square)
        model_file = os.path.basename(gpx_file).split(".")[0] + ".x3d"

    if None in (min_lat, min_long, max_lat, max_long):
        raise InvalidArgumentError("You must specify an area to crop")
    if not model_file:
        raise InvalidArgumentError("model_file must be specified")

    log.debug("Crop boundaries = TL({}, {}) BR({}, {})".format(min_long, max_lat, max_long, min_lat))

    # Get the elevation data
    dem_filename = "/tmp/demdata.tif"
    get_dem_data(dem_filename, min_lat, min_long, max_lat, max_long)

    # Create the model from the elevation data
    log.info("Creating 3D model from elevation data")
    dem_to_model(dem_filename, model_file, z_exaggeration)


def get_dem_data(dem_filename, min_lat, min_long, max_lat, max_long):
    """
    Get the elevation data for the given region. This will download, crop and convert the requested elevation data

    Args:
        dem_filename:   file to save the elevation data to
        min_lat:        south border of the region
        min_long:       east border of the region
        max_lat:        north border of the region
        max_long:       west border of the region
    """
    log = GetLogger()

    workdir = tempfile.mkdtemp()
    log.debug("workdir={}".format(workdir))

    # Figure out which tile we need to download
    upper = int(math.ceil(max_lat))
    left = int(math.floor(max_long))
    basename = "{}{}{}{}".format("n" if upper > 0 else "s",
                                            abs(upper),
                                            "e" if left > 0 else "w",
                                            abs(left))
    remote_filename = basename + ".zip"
    local_filename = os.path.join(workdir, remote_filename)

    # Download the tile
    log.info("Downloading USGS elevation data")
    downloader = HTTPDownloader("prd-tnm.s3.amazonaws.com", port=443)
    downloader.StreamingDownload("StagedProducts/Elevation/13/ArcGrid/{}".format(remote_filename), local_filename, timeout=60*20)

    # Extract the ArcGrid data
    log.info("Extracting elevation data file")
    log.debug("Extracting {} to {}".format(local_filename, workdir))
    archive = zipfile.ZipFile(local_filename)
    archive.extractall(path=workdir)
    input_data = os.path.join(workdir, "grd" + basename + "_13")
    if not os.path.exists(input_data):
        raise ApplicationError("Could not find ArcGrid data in downloaded archive")

    log.info("Converting and cropping elevation data")
    convert_and_crop_raster(input_data, dem_filename, min_lat, min_long, max_lat, max_long)

if __name__ == '__main__':
    parser = ArgumentParser(description="Create a 3D model of terrain")
    area_group = parser.add_argument_group("Area specification", "The area covered by the model can be specified either with a GPX track or by absolute lat/long coordinates")
    area_group.add_argument("-g", "--gpx-file", type=StrType, metavar="FILENAME", help="GPX file to use")
    area_group.add_argument("-p", "--padding", type=float, metavar="MILES", help="Padding to add around the GPX track, in miles")
    area_group.add_argument("-q", "--square", type=float, metavar="MILES", help="Make the region around the GPX track a square")
    area_group.add_argument("-n", "--north", type=float, dest="max_lat", metavar="DEGREES", help="The northern edge of the model, in decimal degrees latitude")
    area_group.add_argument("-s", "--south", type=float, dest="min_lat", metavar="DEGREES", help="The southern edge of the model, in decimal degrees latitude")
    area_group.add_argument("-e", "--east", type=float, dest="max_long", metavar="DEGREES", help="The eastern edge of the model, in decimal degrees longitude")
    area_group.add_argument("-w", "--west", type=float, dest="min_long", metavar="DEGREES", help="The western edge of the model, in decimal degrees longitude")
    parser.add_argument("-z", "--z-exaggeration", type=float, default=1.0, metavar="", help="Amount of z-axis exaggeration to use in the model")
    parser.add_argument("-m", "--model-file", type=StrType, metavar="FILENAME", help="Model file to write out. If not specified, the name will be derived from the GPX file")
    args = parser.parse_args_to_dict()

    app = PythonApp(build_model, args)
    app.Run(**args)
