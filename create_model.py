#!/usr/bin/env python3
"""Import x3d/stl into blender and make a nice model"""

from pathlib import Path
import sys

from pyapputil.appframework import PythonApp
from pyapputil.argutil import ArgumentParser
from pyapputil.typeutil import ValidateAndDefault, StrType
from pyapputil.logutil import GetLogger, logargs
from pyapputil.exceptutil import ApplicationError, InvalidArgumentError
import bpy
from mathutils import Vector #type: ignore #pylint: disable=import-error

from blender_util import extrude_and_flatten, select_obj, get_obj_dimensions, resize_obj, simplify_faces

@logargs
@ValidateAndDefault({
    # "arg_name" : (arg_type, arg_default)
    "mesh_file": (StrType(), None),
    "output_file": (StrType(), None),
    "min_thickness": (float, 0.125),
    "size": (float, None),
})
def refine_model(mesh_file,
			     output_file,
                 min_thickness,
                 size,
):
    log = GetLogger()
    import_path = Path(mesh_file).absolute().resolve()  # blender sometimes crashes with relative paths so make sure they are absolute
    output_path = Path(output_file).absolute().resolve()

    if not import_path.exists():
        raise InvalidArgumentError("Mesh file does not exist")

    # Delete default object
    log.info("Cleaning workspace")
    bpy.ops.object.mode_set(mode="OBJECT")
    for obj in bpy.context.scene.objects:
        if obj.type == "MESH":
            obj.select_set(True)
    bpy.ops.object.delete()

    # Set the display units to inches
    bpy.context.scene.unit_settings.system = 'IMPERIAL'
    bpy.context.scene.unit_settings.length_unit = 'INCHES'

    # Import the model
    log.info(f"Importing {import_path}")
    if import_path.suffix == ".x3d":
        bpy.ops.import_scene.x3d(filepath=str(import_path))
    elif import_path.suffix == ".stl":
        bpy.ops.import_mesh.stl(filepath=str(import_path))
    else:
        raise ApplicationError(f"Unknown file type '{import_path.suffix}'")

    # Select the mesh we just imported
    obj = select_obj()

    # Resize the object
    dims = get_obj_dimensions(obj)
    log.info(f"Imported dimensions: ({dims})")
    idx = 0
    if dims[1] > dims[0]:
        idx = 1
    dim_scale = size / dims[idx]
    log.info(f"Scale factor = {dim_scale}")
    resize_obj(obj, dim_scale)
    dims = get_obj_dimensions(obj)
    log.info(f"Resized dimensions: {dims}")

    # Add thickness
    log.info("Extruding and flattening bottom")
    obj = select_obj()
    extrude_and_flatten(obj, min_thickness)

    # Make the sides of the model a single face each
    simplify_faces(obj)

    # Move the object so that its base is along the Z=0 plane
    log.info("Normalizing object to Z=0 plane")
    log.debug(f"location = {obj.location.z}")
    log.debug(f"w location = {(obj.matrix_world @ obj.location).z}")
    lowest_z = None
    for vert in obj.data.vertices:
        v_world = obj.matrix_world @ Vector((vert.co[0],vert.co[1],vert.co[2]))
        if lowest_z is None or lowest_z > v_world[2]:
            lowest_z = v_world[2]
    obj.location.z = obj.location.z - lowest_z

    log.info(f"Saving {output_path}")
    bpy.ops.wm.save_as_mainfile(filepath=str(output_path))

    log.passed(f"Successfully created model {output_file}")
    return True


def filter_args(argv):
    if not argv:
        return []
    # If this is being invoked inside blender, filter out the blender args and return the rest
    if argv[0].endswith("lender") and "--" in argv:
        return argv[argv.index("--") + 1:]
    return argv[1:]

if __name__ == '__main__':
    parser = ArgumentParser(description="Import x3d/stl mesh and build a nice model")
    parser.add_argument("-m", "--mesh-file", type=StrType(), metavar="FILENAME", help="x3d/stl file to import")
    parser.add_argument("-o", "--output-file", type=StrType(), metavar="FILENAME", help="Blender file to save")
    parser.add_argument("-t", "--min-thickness", type=float, metavar="INCHES", help="Minimum base thickness of the object")
    parser.add_argument("-s", "--size", type=float, metavar="INCHES", help="Size of the long side of the object, other dimensions will be scaled proportionally")

    args = parser.parse_args_to_dict(filter_args(sys.argv))

    app = PythonApp(refine_model, args)
    app.Run(**args)
