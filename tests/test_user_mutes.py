"""Curves the user mutes on chain bones: the simulation ignores them (their channels at rest, as if unkeyed), leaves
them muted, and follows the user's mutes when they change while it simulates."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import check, finish, fresh_import

import bpy
import numpy as np
from mathutils import Quaternion, Vector

addon = fresh_import()
addon.register()
live = sys.modules["waifu_physics.runtime.live"]
scene = bpy.context.scene


def build(keyed=True):
    scene.waifu_physics.simulate = False
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj)
    for action in list(bpy.data.actions):
        bpy.data.actions.remove(action)
    data = bpy.data.armatures.new("rig")
    rig = bpy.data.objects.new("rig", data)
    scene.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    anchor = data.edit_bones.new("anchor")
    anchor.head, anchor.tail = (0, 0, 1.6), (0, 0, 2.0)
    parent, head = anchor, Vector((0, 0, 2.0))
    for i in range(4):
        bone = data.edit_bones.new(f"c{i}")
        bone.head, bone.tail = head, head + Vector((0.15, 0, 0))
        bone.parent = parent
        parent, head = bone, bone.tail.copy()
    bpy.ops.object.mode_set(mode="OBJECT")
    rig.waifu_physics.groups.add().roots.add().name = "c0"
    if keyed:
        bone = rig.pose.bones["c1"]
        bone.rotation_quaternion = Quaternion((1, 0, 0), 0.6)
        bone.keyframe_insert("rotation_quaternion", frame=1)
        bone.rotation_quaternion = Quaternion((1, 0, 0), -0.6)
        bone.keyframe_insert("rotation_quaternion", frame=30)
        bone.rotation_quaternion = (1, 0, 0, 0)
    return rig


def curves(rig):
    return list(rig.animation_data.action.layers[0].strips[0].channelbag(rig.animation_data.action_slot).fcurves)


def play(frames):
    tips = []
    for frame in frames:
        scene.frame_set(frame)
        rt = live.runtime(scene)
        tips.append(rt.system.loc[rt.system.parent >= 0][-1].copy())
    return np.array(tips)


def simulate(rig, frames=range(1, 50)):
    scene.frame_set(1)
    scene.waifu_physics.simulate = True
    tips = play(frames)
    scene.waifu_physics.simulate = False
    return tips


unkeyed = simulate(build(keyed=False))
followed = simulate(build())

# --- muted before simulating: ignored, and left muted
rig = build()
for curve in curves(rig):
    curve.mute = True
ignored = simulate(rig)
check("a chain curve the user muted is ignored: the chain moves as if it had no keys",
      np.abs(ignored - unkeyed).max() < 1e-6, np.abs(ignored - unkeyed).max())
check("... (keys that are not muted are followed)", np.abs(followed - unkeyed).max() > 1.0)
check("... and it stays muted when simulation stops", all(curve.mute for curve in curves(rig)))

# --- unmuted while simulating: taken over and followed from then on
rig = build()
for curve in curves(rig):
    curve.mute = True
scene.frame_set(1)
scene.waifu_physics.simulate = True
play(range(1, 10))
for curve in curves(rig):
    curve.mute = False
bpy.context.view_layer.update()
play(range(10, 12))
keys = live.runtime(scene).rigs[0].keys
check("a curve the user unmutes while simulating is taken over (muted by Waifu Physics, and sampled)",
      len(keys.curves) == 4 and keys.muted and all(curve.mute for curve in curves(rig)), len(keys.curves))
scene.waifu_physics.simulate = False
check("... and is left unmuted when simulation stops", not any(curve.mute for curve in curves(rig)))

# --- one of our own muted curves unmuted while simulating: taken over again, never applied over the physics
rig = build()
scene.frame_set(1)
scene.waifu_physics.simulate = True
play(range(1, 5))
curves(rig)[0].mute = False
bpy.context.view_layer.update()
play(range(5, 7))
keys = live.runtime(scene).rigs[0].keys
check("unmuting a curve Waifu Physics muted to sample only has it taken over again",
      keys.muted and all(curve.mute for curve in curves(rig)) and len(keys.curves) == 4)
scene.waifu_physics.simulate = False
check("... and every curve is unmuted when simulation stops", not any(curve.mute for curve in curves(rig)))

addon.unregister()
finish()
