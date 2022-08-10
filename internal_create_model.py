"""Run a script in Blender to import the mesh and create the model"""

from math import radians
from pathlib import Path
import sys

from pyapputil.appframework import PythonApp
from pyapputil.argutil import ArgumentParser
from pyapputil.typeutil import ValidateAndDefault, StrType
from pyapputil.logutil import GetLogger, logargs
from pyapputil.exceptutil import ApplicationError, InvalidArgumentError
import bpy
import bmesh #type: ignore #pylint: disable=import-error
from mathutils import Euler, Vector #type: ignore #pylint: disable=import-error

from blender_util import (
    ModeSet,
    set_top_view,
    set_zoomed_view,
    set_rendered_view,
    project_uv,
    get_view3d_area_region,
    get_side_faces,
    deselect_all,
    extrude_and_flatten,
    select_obj,
    get_obj_dimensions,
    resize_obj,
    simplify_faces
)

@logargs
@ValidateAndDefault({
    # "arg_name" : (arg_type, arg_default)
    "mesh_file": (StrType(), None),
    "output_file": (StrType(), None),
    "min_thickness": (float, 0.125),
    "size": (float, None),
    "map_image": (StrType(), None),
    "background_image": (StrType(), None),
    "preview_file": (StrType(), None),
    "collada_file": (StrType(), None),
})
def internal_create_model(mesh_file,
                 output_file,
                 min_thickness,
                 size,
                 map_image,
                 background_image,
                 preview_file,
                 collada_file,
):
    log = GetLogger()

    output_file = Path(output_file)
    import_path = Path(mesh_file).resolve()
    output_path = Path(output_file).resolve()

    if not import_path.exists():
        raise InvalidArgumentError("Mesh file does not exist")

    # Make the texture file paths relative to the blender file path. This makes it work inside and outside the container
    background_image = Path(background_image).resolve()
    if not background_image.exists():
        raise InvalidArgumentError("Background image file does not exist")
    map_image = Path(map_image).resolve()
    if not map_image.exists():
        raise InvalidArgumentError("Map image file does not exist")

    try:
        background_image = background_image.relative_to(output_file.parent)
        map_image = map_image.relative_to(output_file.parent)
    except ValueError as ex:
        raise InvalidArgumentError("Image files must be relative subpaths to blender file") from ex

    if not preview_file:
        preview_file = output_file.with_name("preview.png")

    if not collada_file:
        collada_file = output_file.with_suffix(".dae")

    # Do not save multiple versions of the blend file
    bpy.context.preferences.filepaths.save_version = 0
    # Do not save changed preferences on exit
    bpy.context.preferences.use_preferences_save = False

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

    log.info(f"Saving model to {output_path}")
    bpy.ops.wm.save_as_mainfile(filepath=str(output_path))

    log.info("Creating textures")
    # Create a material from the sat image
    map_tex = bpy.data.materials.new("map")
    map_tex.use_nodes = True
    bpy.ops.object.material_slot_add()
    bpy.context.object.active_material = map_tex
    map_tex_img = map_tex.node_tree.nodes.new('ShaderNodeTexImage')
    map_tex_img.image = bpy.data.images.load(f"//{map_image}")
    bsdf = map_tex.node_tree.nodes["Principled BSDF"]
    map_tex.node_tree.links.new(bsdf.inputs['Base Color'], map_tex_img.outputs['Color'])
    log.info(f"Created map texture {map_image}")

    # Create a material from the background
    background_tex = bpy.data.materials.new("back")
    background_tex.use_nodes = True
    bpy.ops.object.material_slot_add()
    bpy.context.object.active_material = background_tex
    back_tex_img = background_tex.node_tree.nodes.new('ShaderNodeTexImage')
    back_tex_img.image = bpy.data.images.load(f"//{background_image}")
    bsdf = background_tex.node_tree.nodes["Principled BSDF"]
    background_tex.node_tree.links.new(bsdf.inputs['Base Color'], back_tex_img.outputs['Color'])
    log.info(f"Created background texture {background_image}")

    log.info("UV mapping textures onto model")
    # Change the view to top/orthographic and project it to a UV map
    layout_screen = bpy.data.workspaces["Layout"].screens[0]
    set_top_view(layout_screen)
    set_zoomed_view(layout_screen)
    project_uv(layout_screen)

    # Set the sides and bottom to the background texture
    deselect_all(layout_screen)
    bpy.ops.object.mode_set(mode="OBJECT")

    # Get the main object
    obj = None
    for obj in bpy.context.view_layer.objects:
        if obj.type == "MESH":
            bpy.context.view_layer.objects.active = obj
            break

    # Setup override for operators
    area, region = get_view3d_area_region(layout_screen)
    space = area.spaces[0]
    override = {}
    override['window'] = bpy.context.window
    override['screen'] = layout_screen
    override['area'] = area
    override['region'] = region
    override['scene'] = bpy.context.scene
    override['space'] = space

    with ModeSet('EDIT'):
        # Select the sides of the object
        bm = bmesh.from_edit_mesh(obj.data)
        sides = get_side_faces(bm)
        bpy.ops.mesh.select_all(override, action='DESELECT')
        for face_name in ("left","right","front","back","bottom"):
            sides[face_name][0].select_set(True)
        bmesh.update_edit_mesh(obj.data)

        # Assign the texture to the selection
        idx = 1
        for idx, slot in enumerate(bpy.context.active_object.material_slots):
            if slot.name == "back":
                break
        bpy.context.object.active_material_index = idx
        bpy.ops.object.material_slot_assign()

        bpy.ops.mesh.select_all(override, action='DESELECT')
        bmesh.update_edit_mesh(obj.data)

    log.info("Updating 3d view")
    # Get out of editing mode and set the view to render the UV mapped material
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action='DESELECT')
    set_rendered_view(layout_screen)

    # Rotate the model to make it easy to look at
    area, region = get_view3d_area_region(layout_screen)
    v3d = area.spaces.active.region_3d
    v3d.view_rotation = Euler((radians(60), 0, radians(45)), 'XYZ').to_quaternion()
    v3d.view_perspective = 'PERSP'

    # Save the changes
    log.info(f"Saving model to {output_path}")
    bpy.ops.wm.save_mainfile()

    # Take a screenshot of the rendered view
    # bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
    log.info("Creating preview")
    bpy.context.scene.render.filepath = preview_file
    bpy.ops.render.opengl(write_still=True)

    log.info("Exporting model")
    # Export a collada file for printing
    bpy.ops.wm.collada_export(filepath=collada_file)

    log.info("Finished building model")

    # Quit blender
    bpy.ops.wm.quit_blender()

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
    parser.add_argument("-a", "--map-image", type=StrType(), metavar="FILENAME", help="image to map over the top of the model")
    parser.add_argument("-b", "--background-image", type=StrType(), metavar="FILENAME", help="image to map over the sides and bottom of the model")
    parser.add_argument("-p", "--preview-file", type=StrType(), metavar="FILENAME", help="path to save preview image")
    parser.add_argument("-c", "--collada-file", type=StrType(), metavar="FILENAME", help="path to save collada export")

    args = parser.parse_args_to_dict(filter_args(sys.argv))

    app = PythonApp(internal_create_model, args)
    app.Run(**args)
