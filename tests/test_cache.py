"""The per-frame cache: replay, resume, uncached frames, invalidation, Cache All, rendering."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import check, finish, fresh_import

import bpy
import numpy as np
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector

swish = fresh_import()
swish.register()
live = sys.modules["swish_physics.runtime.live"]
from swish_physics.data import colliders
scene = bpy.context.scene


def build():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj)
    scene.swish.simulate = False
    scene.frame_start, scene.frame_end = 1, 40
    data = bpy.data.armatures.new("rig")
    rig = bpy.data.objects.new("rig", data)
    scene.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    anchor = data.edit_bones.new("anchor")
    anchor.head, anchor.tail = (0, 0, 1.9), (0, 0, 2.0)
    parent, head = anchor, Vector((0, 0, 2.0))
    for i in range(3):
        bone = data.edit_bones.new(f"c{i}")
        bone.head, bone.tail = head, head + Vector((0.2, 0, 0))
        bone.parent = parent
        parent, head = bone, bone.tail.copy()
    bpy.ops.object.mode_set(mode="OBJECT")
    group = rig.swish.groups.add()
    group.roots.add().name = "c0"
    group.dummy_bone_length = 0.1
    group.damping = 0.05
    return rig, group


def tip(rig):
    return np.array(rig.pose.bones["c2"].tail)


def play(frames):
    return {f: (scene.frame_set(f), tip(rig))[1] for f in frames}


rig, group = build()
scene.frame_set(1)
scene.swish.use_cache = True
scene.swish.simulate = True
played = play(range(1, 41))
rt = live.runtime(scene)
check("playing fills the cache frame by frame", sorted(rt.cache) == list(range(1, 41)), len(rt.cache))
bpy.context.view_layer.update()
check("... and our own writes do not clear it", len(rt.cache) == 40, len(rt.cache))
replayed = {f: (scene.frame_set(f), tip(rig))[1] for f in (25, 7, 33, 40)}
check("scrubbing back replays exactly what was simulated",
      all(np.array_equal(replayed[f], played[f]) for f in replayed), {f: replayed[f] - played[f] for f in replayed})
resumed = play(range(41, 46))

rig, group = build()
scene.frame_set(1)
scene.swish.use_cache = False
scene.swish.simulate = True
straight = play(range(1, 46))
check("playing on past the cache continues exactly as an uninterrupted run",
      all(np.array_equal(resumed[f], straight[f]) for f in resumed), {f: resumed[f] - straight[f] for f in resumed})

rig, group = build()
scene.frame_set(1)
scene.swish.use_cache = True
scene.swish.simulate = True
rest = tip(rig)
play(range(1, 21))
scene.frame_set(35)
check("a frame the cache has not reached shows the unsimulated pose", np.allclose(tip(rig), rest, atol=1e-6),
      (tip(rig), rest))

rt = live.runtime(scene)
check("the cache holds frames 1-20", rt.cached_range() == (1, 20), rt.cached_range())
group.damping = 0.3
check("changing a setting clears the cache", len(rt.cache) == 0, len(rt.cache))
play(range(1, 11))
pb = rig.pose.bones["anchor"]
pb.keyframe_insert("rotation_quaternion", frame=1)
bpy.context.view_layer.update()
check("inserting a keyframe clears the cache", len(live.runtime(scene).cache) == 0, len(live.runtime(scene).cache))
rig.pose.bones["c1"].keyframe_insert("rotation_quaternion", frame=1)
bpy.context.view_layer.update()
rt = live.runtime(scene)
bag = rig.animation_data.action.layers[0].strips[0].channelbag(rig.animation_data.action_slot)
chain_curves = [c for c in bag.fcurves if c.data_path.startswith('pose.bones["c1"]')]
check("a newly keyed chain bone's curves are taken over (muted), so no post-frame replay is needed",
      chain_curves and all(c.mute for c in chain_curves) and not rt.needs_post_replay())
scene.swish.simulate = False
check("... and handed back when simulation stops", all(not c.mute for c in chain_curves))
scene.swish.simulate = True
play(range(1, 11))
ball = colliders.add(rig, "anchor", "Sphere")
play(range(1, 11))
ball.location.x += 0.1
bpy.context.view_layer.update()
check("moving a collider clears the cache", len(live.runtime(scene).cache) == 0, len(live.runtime(scene).cache))
bpy.data.objects.remove(ball)

scene.frame_end = 30
bpy.ops.swish.cache_all()
check("Cache All fills the frame range", live.runtime(scene).cached_range() == (1, 30),
      live.runtime(scene).cached_range())

# --- a render of a cached frame shows the cached pose
bpy.ops.mesh.primitive_cube_add(size=0.06)
marker = bpy.context.object
marker.parent = rig
marker.parent_type = "BONE"
marker.parent_bone = "c2"
marker.matrix_world = rig.matrix_world @ rig.pose.bones["c2"].matrix
marker.color = (1.0, 0.0, 0.0, 1.0)
camera = bpy.data.objects.new("camera", bpy.data.cameras.new("camera"))
scene.collection.objects.link(camera)
camera.location = (0.3, -3.0, 1.6)
camera.rotation_euler = (1.5708, 0.0, 0.0)
scene.camera = camera
scene.render.engine = "BLENDER_WORKBENCH"
scene.display.shading.color_type = "OBJECT"
scene.display.shading.light = "FLAT"
scene.render.resolution_x, scene.render.resolution_y = 320, 240
scene.render.resolution_percentage = 100
scene.world = scene.world or bpy.data.worlds.new("world")
scene.world.color = (0.0, 0.0, 0.0)
bpy.ops.swish.cache_all()
scene.frame_set(30)
expected = world_to_camera_view(scene, camera, marker.matrix_world.translation)
path = os.path.join(tempfile.gettempdir(), "swish_cache_render.png")
scene.render.filepath = path
bpy.ops.render.render(write_still=True)
image = bpy.data.images.load(path)
pixels = np.array(image.pixels[:]).reshape(image.size[1], image.size[0], 4)
red = (pixels[:, :, 0] > 0.5) & (pixels[:, :, 1] < 0.3)
ys, xs = np.nonzero(red)
found = (xs.mean() / image.size[0], ys.mean() / image.size[1]) if len(xs) else None
check("a render of a cached frame shows the cached pose",
      found is not None and abs(found[0] - expected.x) < 0.03 and abs(found[1] - expected.y) < 0.03,
      (found, tuple(expected)[:2]))

scene.swish.simulate = False
swish.unregister()
finish()
