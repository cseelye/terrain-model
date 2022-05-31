#!/usr/bin/env python3
"""Create a 3D mesh of terrain"""

from pathlib import Path

from pyapputil.appframework import PythonApp
from pyapputil.argutil import ArgumentParser
from pyapputil.typeutil import ValidateAndDefault, OptionalValueType, StrType, BoolType
from pyapputil.logutil import GetLogger, logargs
from pyapputil.exceptutil import ApplicationError, InvalidArgumentError

from geo import GPXFile, dem_to_model2, get_cropped_elevation_filename

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
    "dem_filename" : (OptionalValueType(StrType()), None),
    "mesh_file" : (OptionalValueType(StrType()), None),
    "z_exaggeration" : (float, 1.0),
    "cache_dir" : (StrType(), "cache"),
})
def build_mesh(gpx_file,
                padding,
                square,
                min_lat,
                min_long,
                max_lat,
                max_long,
                mesh_file,
                z_exaggeration,
                dem_filename,
                cache_dir):
    """
    Create a 3D mesh of terrain

    Args:
        gpx_file:       (str)   A file containing one of more tracks to use to determine the area of terrain to mesh
        padding:        (float) Padding to add around the GPX track, in miles
        min_lat         (float) Southern boundary of the region to mesh
        min_long        (float) Eastern boundary of the region to mesh
        max_lat         (float) Northern boundary of the region to mesh
        max_long        (float) Western boundary of the region to mesh
        mesh_file:     (str)   File name to write the 3D mesh to
        z_exaggeration: (float) How much Z-axis exaggeration to apply to the mesh
    """
    log = GetLogger()

    # Determine the bounds of the output
    if gpx_file and None in (min_lat, min_long, max_lat, max_long):
        log.info("Parsing GPX file")
        gpx = GPXFile(gpx_file)
        min_lat, min_long, max_lat, max_long = gpx.GetBounds(padding, square)
    if not mesh_file and gpx_file:
        mesh_file = Path(gpx_file).stem + ".stl"

    if not dem_filename and None in (min_lat, min_long, max_lat, max_long):
        raise InvalidArgumentError("You must specify an area")

    log.info(f"mesh boundaries top(max_lat)={max_lat} left(min_long)={min_long} bottom(min_lat)={min_lat} right(max_long)={max_long}")

    # Get the elevation data
    if dem_filename:
        if not Path(dem_filename).exists():
            raise InvalidArgumentError("DEM file does not exist")
        input_file = dem_filename
    else:
        cache_dir = Path(cache_dir)
        dem_filename = Path(get_cropped_elevation_filename(max_lat, min_long, min_lat, max_long))
        input_file = cache_dir / dem_filename
        log.debug(f"Looking for elevation data {input_file}")
        if not (input_file).exists():
            raise ApplicationError("Could not find elevation data")

    # Create the mesh from the elevation data
    log.info("Creating 3D mesh from elevation data")
    dem_to_model2(input_file, mesh_file, z_exaggeration)

    log.passed(f"Successfully created mesh {mesh_file}")
    return True


if __name__ == '__main__':
    parser = ArgumentParser(description="Create a 3D mesh of terrain")
    area_group = parser.add_argument_group("Area specification", "The area covered by the mesh can be specified either with a GPX track or by absolute lat/long coordinates")
    area_group.add_argument("-g", "--gpx-file", type=StrType(), metavar="FILENAME", help="GPX file to use")
    area_group.add_argument("-p", "--padding", type=float, metavar="MILES", help="Padding to add around the GPX track, in miles")
    area_group.add_argument("-q", "--square", action="store_true", help="Make the region around the GPX track a square")
    area_group.add_argument("-n", "--north", type=float, dest="max_lat", metavar="DEGREES", help="The northern edge of the mesh, in decimal degrees latitude")
    area_group.add_argument("-s", "--south", type=float, dest="min_lat", metavar="DEGREES", help="The southern edge of the mesh, in decimal degrees latitude")
    area_group.add_argument("-e", "--east", type=float, dest="max_long", metavar="DEGREES", help="The eastern edge of the mesh, in decimal degrees longitude")
    area_group.add_argument("-w", "--west", type=float, dest="min_long", metavar="DEGREES", help="The western edge of the mesh, in decimal degrees longitude")
    parser.add_argument("-z", "--z-exaggeration", type=float, default=1.0, metavar="", help="Amount of z-axis exaggeration to use in the mesh")
    parser.add_argument("-m", "--mesh-file", type=StrType(), metavar="FILENAME", help="Mesh file to write out. If not specified, the name will be derived from the GPX file")
    parser.add_argument("-i", "--input-dem-file", type=StrType(), dest="dem_filename", metavar="FILENAME", help="Elevation data file to use. If this is not specified, the script will look for the appropriate elevation data in the cache directory instead")
    parser.add_argument("-c", "--cache-dir", type=StrType(), default="cache", metavar="DIRNAME", help="Directory to look for data files in, if input-dem-file was not specified")
    args = parser.parse_args_to_dict()

    app = PythonApp(build_mesh, args)
    app.Run(**args)
