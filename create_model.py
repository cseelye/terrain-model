#!/usr/bin/env python3
"""Import x3d/stl into blender and make a nice model"""

from math import degrees
from pathlib import Path

from pyapputil.appframework import PythonApp
from pyapputil.argutil import ArgumentParser
from pyapputil.typeutil import ValidateAndDefault, StrType
from pyapputil.logutil import GetLogger, logargs
from pyapputil.exceptutil import ApplicationError
import bpy
import bmesh
from mathutils import Vector

from blender_util import METER_TO_INCH, ModeSet, select_obj, get_obj_dimensions, resize_obj, flatten_bottom, simplify_faces

@logargs
@ValidateAndDefault({
    # "arg_name" : (arg_type, arg_default)
    "model_file": (StrType(), None),
    "output_file": (StrType(), None),
    "min_thickness": (float, 0.125),
    "size": (float, None),
})
def refine_model(model_file,
			     output_file,
                 min_thickness,
                 size,
):
    log = GetLogger()
    import_path = Path(model_file).absolute()  # blender crashes with relative paths so make sure they are absolute
    output_path = Path(output_file).absolute()

	# Delete default object
    log.info("Cleaning workspace")
    # with ModeSet("OBJECT"):
    #     for obj in bpy.context.scene.objects:
    #         if obj.type == "MESH":
    #             obj.select_set(True)
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
    log.info("Extruding")
    obj = select_obj()
    with ModeSet("EDIT"):
        bpy.ops.mesh.extrude_region_move(MESH_OT_extrude_region={"use_normal_flip":False,
                                                                 "use_dissolve_ortho_edges":False,
                                                                 "mirror":False},
                                         TRANSFORM_OT_translate={"value":(-0, -0, min_thickness / METER_TO_INCH),
                                                                 "orient_type":'GLOBAL',
                                                                 "orient_matrix":((1, 0, 0), (0, 1, 0), (0, 0, 1)),
                                                                 "orient_matrix_type":'GLOBAL',
                                                                 "constraint_axis":(False, False, True),
                                                                 "mirror":False,
                                                                 "use_proportional_edit":False,
                                                                 "proportional_edit_falloff":'SMOOTH',
                                                                 "proportional_size":1,
                                                                 "use_proportional_connected":False,
                                                                 "use_proportional_projected":False,
                                                                 "snap":False,
                                                                 "snap_target":'CLOSEST',
                                                                 "snap_point":(0, 0, 0),
                                                                 "snap_align":False,
                                                                 "snap_normal":(0, 0, 0),
                                                                 "gpencil_strokes":False,
                                                                 "cursor_transform":False,
                                                                 "texture_space":False,
                                                                 "remove_on_cancel":False,
                                                                 "release_confirm":False,
                                                                 "use_accurate":False,
                                                                 "use_automerge_and_split":False})
        bpy.ops.mesh.select_all(action='DESELECT')
    bpy.ops.object.select_all(action='DESELECT')

    # Flatten the bottom
    log.info("Flattening")
    flatten_bottom(obj)

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

if __name__ == '__main__':
    parser = ArgumentParser(description="Import x3d/stl mesh and build a nice model")
    parser.add_argument("-m", "--model-file", type=StrType(), metavar="FILENAME", help="x3d/stl file to import")
    parser.add_argument("-o", "--output-file", type=StrType(), metavar="FILENAME", help="Blender file to save")
    parser.add_argument("-t", "--min-thickness", type=float, metavar="INCHES", help="Minimum base thickness of the object")
    parser.add_argument("-s", "--size", type=float, metavar="INCHES", help="Size of the long side of the object, other dimensions will be scaled proportionally")
    args = parser.parse_args_to_dict()

    app = PythonApp(refine_model, args)
    app.Run(**args)
