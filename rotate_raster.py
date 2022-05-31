#!/usr/bin/env python3
"""Create a 3D model of terrain"""

from pyapputil.appframework import PythonApp
from pyapputil.argutil import ArgumentParser
from pyapputil.typeutil import IntegerRangeType, ValidateAndDefault, OptionalValueType, StrType, ItemList
from pyapputil.logutil import GetLogger, logargs
from pyapputil.exceptutil import ApplicationError, InvalidArgumentError

from geo import rotate_raster, create_virtual_dataset

@logargs
@ValidateAndDefault({
    # "arg_name" : (arg_type, arg_default)
    "input_files" : (OptionalValueType(ItemList(StrType())), None),
    "output_filename" : (StrType(), None),
    "rotation_degrees" : (IntegerRangeType(-359, 359), None),
})
def rotate(input_files,
           output_filename,
           rotation_degrees):
    """
    Rotate a raster file a given number of degrees around its center

    Args:
        input_filename:     (str) The raster file to rotate
        output_filename:    (str) The output file to write with the rotated raster
        rotation_degrees    (float) The number of degrees to rotate the raster
    """
    log = GetLogger()

    input_filename = create_virtual_dataset(input_files)
    rotate_raster(input_filename, output_filename, rotation_degrees)

    log.passed(f"Successfully rotated {input_filename} by {rotation_degrees} into {output_filename}")
    return True


if __name__ == '__main__':
    parser = ArgumentParser(description="Create a 3D model of terrain")
    parser.add_argument("-i", "--input-file", required=True, dest="input_files", type=StrType(), action="append", metavar="FILENAME", help="One or more input files, in a raster format that GDAL can read.")
    parser.add_argument("-o", "--output-file", required=True, type=StrType(), dest="output_filename", metavar="FILENAME", help="Output file to write with the rotated raster")
    parser.add_argument("-r", "--rotation", required=True, type=IntegerRangeType(-359, 359), dest="rotation_degrees", metavar="DEGREES", help="Degrees to rotate the image")
    args = parser.parse_args_to_dict()

    app = PythonApp(rotate, args)
    app.Run(**args)
