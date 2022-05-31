"""Run a script in Blender to finish the model"""

from math import degrees
from pathlib import Path
import sys

from pyapputil.appframework import PythonApp
from pyapputil.argutil import ArgumentParser
from pyapputil.typeutil import ValidateAndDefault, StrType
from pyapputil.logutil import GetLogger, logargs
from pyapputil.exceptutil import ApplicationError
import bpy
import bmesh
from math import radians
from mathutils import Euler #type: ignore #pylint: disable=import-error

from blender_util import (
    ModeSet,
    set_top_view,
    set_zoomed_view,
    set_rendered_view,
    project_uv,
    get_view3d_area_region,
    get_side_faces,
    deselect_all
)

@logargs
@ValidateAndDefault({
    # "arg_name" : (arg_type, arg_default)
    "map_image": (StrType(), None),
    "background_image": (StrType(), None),
    "preview_file": (StrType(), None),
    "collada_file": (StrType(), None),
})
def finish_model(map_image,
			     background_image,
                 preview_file,
                 collada_file,
):
    log = GetLogger()

    log.info("Creating textures")
    # Create a material from the sat image
    map_tex = bpy.data.materials.new("map")
    map_tex.use_nodes = True
    bpy.ops.object.material_slot_add()
    bpy.context.object.active_material = map_tex
    map_tex_img = map_tex.node_tree.nodes.new('ShaderNodeTexImage')
    map_tex_img.image = bpy.data.images.load(map_image)
    bsdf = map_tex.node_tree.nodes["Principled BSDF"]
    map_tex.node_tree.links.new(bsdf.inputs['Base Color'], map_tex_img.outputs['Color'])

    # Create a material from the background
    background_tex = bpy.data.materials.new("back")
    background_tex.use_nodes = True
    bpy.ops.object.material_slot_add()
    bpy.context.object.active_material = background_tex
    back_tex_img = background_tex.node_tree.nodes.new('ShaderNodeTexImage')
    back_tex_img.image = bpy.data.images.load(background_image)
    bsdf = background_tex.node_tree.nodes["Principled BSDF"]
    background_tex.node_tree.links.new(bsdf.inputs['Base Color'], back_tex_img.outputs['Color'])

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
    bpy.ops.wm.save_mainfile()

    # Take a screenshot of the rendered view
    # bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
    bpy.context.scene.render.filepath = preview_file
    bpy.ops.render.opengl(write_still=True)

    # Export a collada file for printing
    bpy.ops.wm.collada_export(filepath=collada_file)

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
    parser.add_argument("-m", "--map-image", type=StrType(), metavar="FILENAME", help="image to map over the top of the model")
    parser.add_argument("-b", "--background-image", type=StrType(), metavar="FILENAME", help="image to map over the sides and bottom of the model")
    parser.add_argument("-p", "--preview-file", type=StrType(), metavar="FILENAME", help="path to save preview image")
    parser.add_argument("-c", "--collada-file", type=StrType(), metavar="FILENAME", help="path to save collada export")

    args = parser.parse_args_to_dict(filter_args(sys.argv))

    app = PythonApp(finish_model, args)
    app.Run(**args)
