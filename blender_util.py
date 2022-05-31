"""Helper functions for working with blender models"""

from math import degrees

from pyapputil.logutil import GetLogger

import bpy
import bmesh
from mathutils import Vector

METER_TO_INCH = 39.37007874

class ModeSet:
    """Context manager to set the context mode"""
    def __init__(self, new_mode):
        self.old_mode = bpy.context.object.mode
        self.new_mode = new_mode
    def __enter__(self):
        self.old_mode = bpy.context.object.mode
        bpy.ops.object.mode_set(mode=self.new_mode)
        return self.new_mode
    def __exit__(self, exc_type, exc_value, exc_tb):
        bpy.ops.object.mode_set(mode=self.old_mode)

def get_obj():
    with ModeSet("EDIT"):
        bpy.ops.object.select_all(action='DESELECT')
        for obj in bpy.context.view_layer.objects:
            if obj.type == "MESH":
                return obj
        return None

def select_obj():
    bpy.ops.object.select_all(action='DESELECT')
    for obj in bpy.context.view_layer.objects:
        if obj.type == "MESH":
            bpy.context.view_layer.objects.active = obj
            obj.select_set(True)
            return obj
    return None

def get_obj_dimensions(obj):
    return obj.dimensions[0] * METER_TO_INCH, obj.dimensions[1] * METER_TO_INCH, obj.dimensions[2] * METER_TO_INCH

def resize_obj(obj, scale):
    obj.dimensions = obj.dimensions[0] * scale, obj.dimensions[1] * scale, obj.dimensions[2] * scale
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)

def select_bottom_faces(obj):
    with ModeSet("EDIT"):
        bm = bmesh.from_edit_mesh(obj.data)
        bm.select_mode = {'FACE'}
        down_vec = Vector([0,0,-1])
        for face in bm.faces:
            ang = degrees(face.normal.angle(down_vec))
            if ang >= 0 and ang < 90:
                face.select_set(True)
            else:
                face.select_set(False)
        bm.select_flush_mode()
        bmesh.update_edit_mesh(obj.data)

def select_bottom_edges(obj):
    with ModeSet("EDIT"):
        bm = bmesh.from_edit_mesh(obj.data)
        bm.select_mode = {'EDGE', 'FACE'}
        down_vec = Vector([0,0,-1])
        for face in bm.faces:
            ang = degrees(face.normal.angle(down_vec))
            if ang >= 0 and ang < 90:
                face.select_set(True)
            else:
                face.select_set(False)
        bm.select_flush_mode()
        bmesh.update_edit_mesh(obj.data)

def get_bottom_faces(obj_mesh):
    with ModeSet("EDIT"):
        down_vec = Vector([0,0,-1])
        # Find all faces that are less than 90 degrees away from a straight down vector
        bottom = []
        for face in obj_mesh.faces:
            ang = degrees(face.normal.angle(down_vec))
            if ang >= 0 and ang < 90:
                bottom.append(face)
        return bottom

def get_top_faces(obj_mesh):
    with ModeSet("EDIT"):
        up_vec = Vector([0,0,1])
        # Find all faces that are less than 90 degrees away from a straight up vector
        top = []
        for face in obj_mesh.faces:
            ang = degrees(face.normal.angle(up_vec))
            if ang >= 0 and ang < 90:
                top.append(face)
        return top

def get_side_faces(obj_mesh):
    with ModeSet("EDIT"):
        vectors = {
            "front": Vector([0, -1, 0]),
            "back": Vector([0, 1, 0]),
            "left": Vector([-1, 0, 0]),
            "right": Vector([1, 0, 0]),
            "bottom": Vector([0, 0, -1]),
        }
        sides = {
            "front": [],
            "back": [],
            "left": [],
            "right": [],
            "top": [],
            "bottom": []
        }
        for face in obj_mesh.faces:
            found = False
            for side, vec in vectors.items():
                ang = degrees(face.normal.angle(vec))
                if ang == 0:
                    sides[side].append(face)
                    found = True
                    break
            if not found:
                sides["top"].append(face)
    return sides

def flatten_bottom(obj):
    with ModeSet("EDIT"):
        bm = bmesh.from_edit_mesh(obj.data)

        # Make a list of the "bottom" faces of the object
        bottom = get_bottom_faces(bm)

        # Get the lowest Z val from all the bottom faces
        min_z = None
        for face in bottom:
            for v in face.verts:
                if min_z is None:
                    min_z = v.co.z
                    continue
                if v.co.z < min_z:
                    min_z = v.co.z

        # Set the Z value on all the bottom faces equal to the lowest
        for face in bottom:
            for v in face.verts:
                v.co = v.co[0], v.co.y, min_z

def extrude_and_flatten(obj, min_thickness):
    extrude_amount = min_thickness / METER_TO_INCH
    with ModeSet("EDIT"):
        bpy.ops.mesh.extrude_region_move(MESH_OT_extrude_region={"use_normal_flip":False,
                                                                 "use_dissolve_ortho_edges":False,
                                                                 "mirror":False},
                                         TRANSFORM_OT_translate={"value":(-0, -0, extrude_amount),
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

    flatten_bottom(obj)

def get_bounding_box(obj):
    with ModeSet("EDIT"):
        min_x = max_x = min_y = max_y = min_z = max_z = None
        bm = bmesh.from_edit_mesh(obj.data)
        # bottom = get_bottom_faces(bm)
        # for face in bottom:
        for face in bm.faces:
            for v in face.verts:
                if min_x is None:
                    min_x = v.co.z
                if max_x is None:
                    max_x = v.co.z
                if min_y is None:
                    min_y = v.co.z
                if max_y is None:
                    max_y = v.co.z
                if min_z is None:
                    min_z = v.co.z

                if v.co.x > max_x:
                    max_x = v.co.x
                if v.co.y > max_y:
                    max_y = v.co.y
                if v.co.x < min_x:
                    min_x = v.co.x
                if v.co.y < min_y:
                    min_y = v.co.y
                if v.co.z < min_z:
                    min_z = v.co.z
        # top = get_top_faces(bm)
        # for face in top:
        #     for v in face.verts:
        #         if max_z is None:
        #             max_z = v.co.z
        #             continue
        #         if v.co.z > max_z:
        #             max_z = v.co.z
        return min_x, min_y, min_z, max_x, max_y, max_z

def print_verts(verts):
    log = GetLogger()
    prefix = "Point " if len(verts) == 1 else "Points"
    for idx, v in enumerate(verts):
        log.info("    {}: ({: >27.24f},{: >27.24f},{: >27.24f})".format(prefix if idx == 0 else "      ",
                                                                     v.co.x, v.co.y, v.co.z))

def print_selected(bm):
    log = GetLogger()
    # selected_faces = set()
    selected_edges = set()
    selected_verts = set()
    for face in bm.faces:
        for edge in face.edges:
            if edge.select:
                selected_edges.add(edge.index)
            for v in edge.verts:
                if v.select:
                    selected_verts.add(v.index)
    for e_idx in sorted(selected_edges):
        log.info(f"    Edge {e_idx}")
    for v_idx in sorted(selected_verts):
        log.info(f"    Vert {v_idx}")

def simplify_faces(obj):
    log = GetLogger()
    min_x, min_y, _, max_x, max_y, _ = get_bounding_box(obj)

    with ModeSet('EDIT'):
        bpy.ops.mesh.select_all(action='DESELECT')
        bm = bmesh.from_edit_mesh(obj.data)
        for face_name in ("left","right","front","back","bottom"):
            log.info(f"Simplifying {face_name} side faces")
            face_limit = 25000
            sides = get_side_faces(bm)
            previous_face_count = None
            while True:
                sides = get_side_faces(bm)
                log.info(f"  Face count = {len(sides[face_name])}")
                if len(sides[face_name]) <= 1 or len(sides[face_name]) == previous_face_count:
                    break
                bpy.ops.mesh.select_all(action='DESELECT')
                face_count = 0
                for face in sides[face_name]:
                    for edge in face.edges:
                        # Select the edges where the angle of the faces is 0
                        # This will only select an edge where the two faces are perfectly flat
                        angle_rad = edge.calc_face_angle()
                        if round(angle_rad, 3) == 0.0:
                            edge.select_set(True)
                            for v in edge.verts:
                                v.select_set(True)
                    face_count += 1
                    if face_count >= face_limit or face_count >= len(sides[face_name]):
                        previous_face_count = len(sides[face_name])
                        bmesh.update_edit_mesh(obj.data)
                        bpy.ops.mesh.dissolve_edges()
                        face_count = 0
                        break

def get_view3d_area_region(screen):
    for area in screen.areas:
        if area.type == "VIEW_3D":
            for region in area.regions:
                if region.type == "WINDOW":
                    return area, region

def set_top_view(screen):
    area, region = get_view3d_area_region(screen)
    override = bpy.context.copy()
    override['area'] = area
    override['region'] = region
    bpy.ops.view3d.view_axis(override, type='TOP')

def set_zoomed_view(screen):
    select_obj()
    area, region = get_view3d_area_region(screen)
    override = bpy.context.copy()
    override['area'] = area
    override['region'] = region
    # bpy.ops.view3d.select_box(override, xmin=0,xmax=area.width,ymin=0,ymax=area.height,mode='ADD')
    bpy.ops.view3d.view_selected(override, use_all_regions=False)
        # for obj in bpy.data.objects:
        #     obj.select_set(False)

def set_rendered_view(screen):
    area, region = get_view3d_area_region(screen)
    override = bpy.context.copy()
    override['area'] = area
    override['region'] = region
    bpy.ops.view3d.toggle_shading(override, type='RENDERED')

def project_uv(screen):
    area, region = get_view3d_area_region(screen)
    space = area.spaces[0]

    # override = bpy.context.copy()
    override = {}
    override['window'] = bpy.context.window
    override['screen'] = screen
    override['area'] = area
    override['region'] = region
    override['scene'] = bpy.context.scene
    override['space'] = space
    # Make sure we are in orthographic view
    if space.region_3d.view_perspective != "ORTHO":
        bpy.ops.view3d.view_persportho(override)

    # Select all
    bpy.ops.object.mode_set(override, mode='EDIT')
    bpy.ops.mesh.select_all(override, action='SELECT')

    # Update view
    space.region_3d.update()

    # Create projection
    bpy.ops.uv.project_from_view(override,
                                    orthographic=False,
                                    camera_bounds=True,
                                    correct_aspect=True,
                                    clip_to_bounds=False,
                                    scale_to_bounds=True)

def deselect_all(screen):
    area, region = get_view3d_area_region(screen)
    override = {}#bpy.context.copy()
    override['area'] = area
    override['region'] = region
    with ModeSet("OBJECT"):
        bpy.ops.object.select_all(override, action='DESELECT')
