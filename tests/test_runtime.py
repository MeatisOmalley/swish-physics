"""Live simulation on real armatures: input pose, stepping, writing back, resets."""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import check, finish, fresh_import

import bpy
import numpy as np
from mathutils import Vector

swish = fresh_import()
swish.register()
live = sys.modules["swish_physics.runtime.live"]
scene = bpy.context.scene


def armature(name, direction=(1.0, 0.0, 0.0), count=3, length=0.2):
    """An anchor bone and a chain of `count` bones from (0, 0, 2) along `direction`."""
    data = bpy.data.armatures.new(name)
    obj = bpy.data.objects.new(name, data)
    scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    anchor = data.edit_bones.new("anchor")
    anchor.head, anchor.tail = (0.0, 0.0, 1.9), (0.0, 0.0, 2.0)
    parent, head = anchor, Vector((0.0, 0.0, 2.0))
    for i in range(count):
        bone = data.edit_bones.new(f"c{i}")
        bone.head = head
        bone.tail = head + Vector(direction) * length
        bone.parent = parent
        bone.use_connect = i > 0
        parent, head = bone, bone.tail.copy()
    bpy.ops.object.mode_set(mode="OBJECT")
    return obj


def group(obj, **values):
    g = obj.swish.groups.add()
    g.roots.add().name = "c0"
    g.dummy_bone_length = 0.1
    for key, value in values.items():
        setattr(g, key, value)
    return g


def heads(obj):
    return np.array([obj.pose.bones[f"c{i}"].head for i in range(3)] + [obj.pose.bones["c2"].tail])


def play(start, end):
    for frame in range(start, end + 1):
        scene.frame_set(frame)


def reset_scene():
    scene.swish.simulate = False
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj)
    scene.frame_start = 1
    scene.frame_set(1)


# --- a chain falls, keeps its lengths, and Blender's bones sit exactly on the solver's points
reset_scene()
rig = armature("fall")
group(rig, damping=0.05, stiffness=0.0)
scene.swish.simulate = True
start = heads(rig)
play(1, 40)
now = heads(rig)
lengths = np.linalg.norm(np.diff(now, axis=0), axis=1)
check("a horizontal chain falls under gravity", now[-1, 2] < start[-1, 2] - 0.2, (start[-1], now[-1]))
check("... its root stays put", np.allclose(now[0], start[0], atol=1e-6), now[0])
check("... every bone keeps its length", np.allclose(lengths, 0.2, atol=1e-5), lengths)
rt = live.runtime(scene)
s = rt.system
points = s.loc[rt.real] / rt.cm
bones = [rig.pose.bones[rt.rigs[0].names[b]].head for b in rt.bone_of_point]
check("Blender's bone heads sit on the solver's points", np.allclose(points, np.array(bones), atol=1e-5),
      np.abs(points - np.array(bones)).max())
check("the rotations were written as the bones' own mode (quaternion)",
      all(rig.pose.bones[f"c{i}"].rotation_mode == "QUATERNION" for i in range(3)))

# --- turning simulation off puts unkeyed chain bones back at rest
scene.swish.simulate = False
bpy.context.view_layer.update()
check("turning Simulate off restores the rest pose", np.allclose(heads(rig), start, atol=1e-6), heads(rig))

# --- stiffness 1 holds an unkeyed chain at rest; a keyed chain follows its animation
reset_scene()
rig = armature("held")
group(rig, stiffness=1.0)
scene.swish.simulate = True
rest = heads(rig)
play(1, 20)
check("stiffness 1 holds an unkeyed chain at its rest against gravity", np.allclose(heads(rig), rest, atol=1e-5),
      heads(rig))

reset_scene()
rig = armature("keyed")
g = group(rig, stiffness=1.0)
pb = rig.pose.bones["c1"]
pb.rotation_mode = "XYZ"
pb.rotation_euler = (0.0, 0.0, 0.0)
pb.keyframe_insert("rotation_euler", frame=1)
pb.rotation_euler = (0.0, 0.0, 1.2)
pb.keyframe_insert("rotation_euler", frame=20)
scene.frame_set(20)
animated = heads(rig)              # the animated pose, before any simulation
scene.frame_set(1)
scene.swish.simulate = True
play(1, 20)
check("a keyed chain bone is read as input: stiffness 1 follows the animation",
      np.allclose(heads(rig), animated, atol=1e-4), (heads(rig), animated))
check("... and keeps its Euler rotation mode", pb.rotation_mode == "XYZ")

# --- every rotation mode survives, and the result is the same whatever the modes
tails = {}
for modes in (("QUATERNION", "QUATERNION", "QUATERNION"), ("XYZ", "AXIS_ANGLE", "ZXY")):
    reset_scene()
    rig = armature("modes")
    group(rig, damping=0.1)
    for i, mode in enumerate(modes):
        rig.pose.bones[f"c{i}"].rotation_mode = mode
    scene.swish.simulate = True
    play(1, 25)
    tails[modes] = heads(rig)[-1].copy()
    check(f"rotation modes {modes} are kept", tuple(rig.pose.bones[f"c{i}"].rotation_mode for i in range(3)) == modes)
    scene.swish.simulate = False
quat, mixed = tails.values()
check("... and the chain moves the same in any mode", np.allclose(quat, mixed, atol=1e-5), (quat, mixed))

# --- keyframed damping takes effect as it plays
runs = {}
for keyed in (False, True):
    reset_scene()
    rig = armature("damped")
    g = group(rig, damping=0.0)
    if keyed:
        g.damping = 0.0
        rig.keyframe_insert('swish.groups[0].damping', frame=1)
        g.damping = 0.9
        rig.keyframe_insert('swish.groups[0].damping', frame=15)
    scene.swish.simulate = True
    play(1, 40)
    runs[keyed] = heads(rig)[-1].copy()
check("keyframed damping changes the motion as it plays", not np.allclose(runs[False], runs[True], atol=1e-3), runs)

# --- world damping 0: moving the armature leaves the chain trailing; a teleport does not fling it
reset_scene()
rig = armature("moving", direction=(0.0, 0.0, -1.0))
group(rig, stiffness=0.0, world_damping_location=0.0, world_damping_rotation=0.0, damping=0.2)
scene.swish.simulate = True
play(1, 5)
for frame in range(6, 12):
    rig.location.x += 0.05
    scene.frame_set(frame)
trail = heads(rig)[-1, 0]
check("with world damping 0, a chain trails behind its moving armature", trail < -0.01, trail)
before = heads(rig)[-1].copy()
rig.location.x += 20.0
scene.frame_set(12)
after = heads(rig)[-1]
check("a 20 m jump is a teleport: the chain keeps its shape", abs(after[0] - before[0]) < 0.05, (before, after))

# --- jumping back resets to the pose
reset_scene()
rig = armature("jump")
group(rig)
scene.swish.simulate = True
rest = heads(rig)
play(1, 30)
scene.frame_set(10)
check("jumping backwards starts over from the pose", np.allclose(heads(rig), rest, atol=1e-6), heads(rig))

scene.swish.simulate = False
swish.unregister()
check("unregistering removes the frame handler", live._frame_changed not in bpy.app.handlers.frame_change_post)
finish()
