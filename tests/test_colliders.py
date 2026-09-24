"""Collider objects: drawn exactly as they collide, pushing chains, shared across armatures."""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import check, finish, fresh_import

import bpy
import numpy as np
from mathutils import Matrix, Quaternion, Vector

addon = fresh_import()
addon.register()
from waifu_physics.data import colliders
from waifu_physics.solver import uemath as ue
live = sys.modules["waifu_physics.runtime.live"]
scene = bpy.context.scene
CM = 100.0


def armature(name, chain=True):
    data = bpy.data.armatures.new(name)
    obj = bpy.data.objects.new(name, data)
    scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    anchor = data.edit_bones.new("anchor")
    anchor.head, anchor.tail = (0, 0, 1.6), (0, 0, 2.0)
    if chain:
        parent, head = anchor, Vector((0, 0, 2.0))
        for i in range(4):
            bone = data.edit_bones.new(f"c{i}")
            bone.head, bone.tail = head, head + Vector((0.15, 0, 0))
            bone.parent = parent
            parent, head = bone, bone.tail.copy()
    bpy.ops.object.mode_set(mode="OBJECT")
    return obj


def world_vertices(obj):
    bpy.context.view_layer.update()
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    points = np.array([obj.matrix_world @ v.co for v in mesh.vertices])
    evaluated.to_mesh_clear()
    return points


# --- each shape is drawn exactly where the solver collides, even with an uneven object scale
rig = armature("rig")
col = colliders.add(rig, "anchor", "Sphere")
check("a collider is a wire mesh parented to its bone, never rendered",
      col.parent == rig and col.parent_type == "BONE" and col.parent_bone == "anchor" and col.hide_render
      and col.display_type == "WIRE" and col.waifu_physics_collider.is_collider)
col.scale = (1.5, 0.8, 1.2)
col.rotation_euler = (0.3, -0.2, 0.5)
for shape in colliders.SHAPES:
    colliders.set_value(col, "Shape", shape)
    colliders.set_value(col, "Radius", 0.05)
    colliders.set_value(col, "Radius 1", 0.09)
    colliders.set_value(col, "Length", 0.3)
    colliders.set_value(col, "Extent", (0.04, 0.06, 0.08))
    points = world_vertices(col)
    s = colliders.shape_of(col, rig, CM)
    rot = np.array(s.rotation)[None]
    centre = np.array(s.location) / CM
    local = ue.unrotate_vector(np.repeat(rot, len(points), 0), points - centre)   # the shape's own frame
    if shape in ("Sphere", "Inner Sphere"):
        error = np.abs(np.linalg.norm(local, axis=1) - s.radius / CM).max()
    elif shape == "Capsule":
        half = s.length / CM / 2
        axial = np.clip(local[:, 2], -half, half)
        error = np.abs(np.linalg.norm(local - np.stack([0 * axial, 0 * axial, axial], 1), axis=1) - s.radius / CM).max()
    elif shape == "Tapered Capsule":
        half = s.length / CM / 2
        top = local[:, 2] > 0
        ends = np.where(top, half, -half)
        radius = np.where(top, s.radius, s.radius1) / CM
        error = np.abs(np.linalg.norm(local - np.stack([0 * ends, 0 * ends, ends], 1), axis=1) - radius).max()
    elif shape == "Box":
        error = np.abs(np.abs(local) - np.array(s.extent) / CM).max()
    else:
        on_plane = np.abs(local[:, 2]) < 1e-6
        error = 0.0 if on_plane.sum() >= 4 else 1.0
    check(f"{shape}: drawn on the surface the solver collides with, scaled (1.5, 0.8, 1.2)", error < 1e-5, error)

# --- capsules run along the collider's Y (the bone), which is Kawaii's Z
col.scale = (1, 1, 1)
col.rotation_euler = (0, 0, 0)
col.matrix_world = rig.matrix_world @ rig.pose.bones["anchor"].matrix
colliders.set_value(col, "Shape", "Capsule")
s = colliders.shape_of(col, rig, CM)
axis_z = ue.axis(np.array(s.rotation)[None], 2)[0]
bone_y = np.array(rig.pose.bones["anchor"].matrix.to_3x3().col[1])
check("a capsule lies along its bone: Kawaii's capsule axis is the bone's Y", np.allclose(axis_z, bone_y, atol=1e-6),
      (axis_z, bone_y))

# --- a falling chain rests on a capsule under it
bpy.data.objects.remove(col)
g = rig.waifu_physics.groups.add()
g.roots.add().name = "c0"
g.dummy_bone_length = 0.1
g.radius = 0.02
g.damping = 0.1
floor = colliders.add(rig, "anchor", "Capsule")
floor.matrix_world = Matrix.Translation((0.35, 0.0, 1.8)) @ Matrix.Rotation(math.pi / 2, 4, "Z")
colliders.set_value(floor, "Radius", 0.06)
colliders.set_value(floor, "Length", 0.8)
scene.frame_set(1)
scene.waifu_physics.simulate = True
for frame in range(1, 60):
    scene.frame_set(frame)
rt = live.runtime(scene)
check("the group collides with its armature's collider", len(rt.system.shape_type) == 1, len(rt.system.shape_type))
shape = colliders.shape_of(floor, rig, CM)
start = np.array(shape.location) + ue.axis(np.array(shape.rotation)[None], 2)[0] * shape.length / 2
end = np.array(shape.location) - ue.axis(np.array(shape.rotation)[None], 2)[0] * shape.length / 2
moving = rt.system.loc[rt.system.parent >= 0]
segment = end - start
t = np.clip(((moving - start) @ segment) / (segment @ segment), 0, 1)
gaps = np.linalg.norm(moving - (start + t[:, None] * segment), axis=1) - shape.radius
check("every chain point stays a point radius clear of the capsule", gaps.min() > 2.0 - 1e-3, gaps.min())
check("... after resting on it (the chain fell onto it)", (rt.system.loc[:, 2] < 200.0 - 1.0).any())

# --- disabled colliders are ignored
floor.waifu_physics_collider.enabled = False
scene.frame_set(60)
check("a disabled collider is left out", len(rt.system.shape_type) == 0, len(rt.system.shape_type))
scene.waifu_physics.simulate = False

# --- a group can use another armature's colliders, placed in its own armature's space
body = armature("body", chain=False)
ball = colliders.add(body, "anchor", "Sphere")
body.location = (1.0, 2.0, 0.0)
rig.rotation_euler = (0.0, 0.0, 0.7)
g.collider_sets.add().armature = body
scene.waifu_physics.simulate = True
scene.frame_set(2)
rt = live.runtime(scene)
shapes = rt.system.shape_type
bpy.context.view_layer.update()
expected = (rig.matrix_world.inverted() @ ball.matrix_world).translation * CM
check("another armature's collider joins the group through a collider set", len(shapes) == 1, len(shapes))
check("... in the group's armature space", np.allclose(rt.system.shape_loc[0], expected, atol=1e-4),
      (rt.system.shape_loc[0], tuple(expected)))
scene.waifu_physics.simulate = False

# --- keyframed collider sizes: the solver reads the animated value, and the drawing follows
md, ident = colliders.input_path(ball, "Radius")
socket = getattr(md.properties.inputs, ident)
socket.value = 0.05
ball.keyframe_insert(socket.path_from_id("value"), frame=1)
socket.value = 0.2
ball.keyframe_insert(socket.path_from_id("value"), frame=10)
scene.frame_set(10)
radius_at_10 = colliders.shape_of(ball, body, CM).radius
drawn = world_vertices(ball)
drawn_radius = np.linalg.norm(drawn - np.array(ball.matrix_world.translation), axis=1).max()
check("a keyframed collider radius reaches the solver", abs(radius_at_10 - 20.0) < 1e-3, radius_at_10)
check("... and the drawing follows it", abs(drawn_radius - 0.2) < 1e-4, drawn_radius)

addon.unregister()
finish()
