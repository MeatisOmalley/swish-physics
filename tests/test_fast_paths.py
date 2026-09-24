"""One evaluation a frame: taken-over keys, the before-frame live solve, saving and the lean bake."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import check, finish, fresh_import

import bpy
import numpy as np
from mathutils import Vector

swish = fresh_import()
swish.register()
live = sys.modules["swish_physics.runtime.live"]
keys = sys.modules["swish_physics.runtime.keys"]
scene = bpy.context.scene


def build(keyed=True):
    scene.swish.simulate = False
    scene.swish.use_cache = False
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj)
    for action in list(bpy.data.actions):
        bpy.data.actions.remove(action)
    scene.frame_start, scene.frame_end = 1, 40
    data = bpy.data.armatures.new("rig")
    rig = bpy.data.objects.new("rig", data)
    scene.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    head = data.edit_bones.new("head")
    head.head, head.tail = (0, 0, 1.5), (0, 0, 1.7)
    parent, at = head, Vector((0, -0.1, 1.6))
    for i in range(4):
        bone = data.edit_bones.new(f"h{i}")
        bone.head, bone.tail = at, at + Vector((0, -0.02, -0.1))
        bone.parent = parent
        parent, at = bone, bone.tail.copy()
    bpy.ops.object.mode_set(mode="OBJECT")
    for frame, x in ((1, 0.0), (20, 0.4), (40, 0.0)):
        rig.location.x = x
        rig.keyframe_insert("location", index=0, frame=frame)
    if keyed:
        pb = rig.pose.bones["h1"]
        pb.rotation_mode = "XYZ"
        pb.keyframe_insert("rotation_euler", frame=1)
        pb.rotation_euler = (0.0, 0.0, 0.6)
        pb.keyframe_insert("rotation_euler", frame=40)
        pb.rotation_euler = (0.0, 0.0, 0.0)
    group = rig.swish.groups.add()
    group.roots.add().name = "h0"
    group.dummy_bone_length = 0.05
    return rig


def chain_curves(rig):
    bag = rig.animation_data.action.layers[0].strips[0].channelbag(rig.animation_data.action_slot)
    return [c for c in bag.fcurves if c.data_path.startswith('pose.bones["h')]


# --- taken-over keys
rig = build()
curves = chain_curves(rig)
scene.frame_set(1)
scene.swish.simulate = True
rt = live.runtime(scene)
check("simulating takes the chain's keys over: their curves are muted", curves and all(c.mute for c in curves))
check("... the object's own animation is left alone",
      not any(c.mute for c in rig.animation_data.action.layers[0].strips[0]
              .channelbag(rig.animation_data.action_slot).fcurves if not c.data_path.startswith("pose")))
check("... and the rig can take the one-evaluation path", rt.fast)

# --- live: playing on solves before the frame; a jump takes the exact path
paths = []
for f in range(2, 21):
    scene.frame_set(f)
    paths.append(rt.stepped_ahead == f)
check("playing on, every frame is solved before Blender evaluates it", all(paths), paths)
turn = rig.pose.bones["h1"].rotation_euler.z
scene.frame_set(35)
check("a jump is not solved ahead (it resets from a freshly evaluated pose)", rt.stepped_ahead is None)
check("the keyed bone still turns toward its keys while simulating", turn > 0.1, turn)

# --- saving: the file gets the keys unmuted, the session keeps them taken over
path = os.path.join(tempfile.gettempdir(), "swish_fast_paths.blend")
bpy.ops.wm.save_as_mainfile(filepath=path, copy=True)
check("after saving, the keys are taken over again", all(c.mute for c in curves))
scene.swish.simulate = False
check("stopping the simulation hands the keys back", not any(c.mute for c in curves)
      and keys.MARK not in rig.keys())

# --- a crash while simulating: the next load unmutes what Swish had muted
rig = build()
scene.frame_set(1)
scene.swish.simulate = True
curves = chain_curves(rig)
check("the armature remembers what Swish muted", keys.MARK in rig.keys())
keys.recover(bpy.data.objects)           # what load_post does with a file saved mid-simulation
check("recovery unmutes those curves and forgets them", not any(c.mute for c in curves) and keys.MARK not in rig.keys())
scene.swish.simulate = False

# --- an NLA-animated chain bone cannot be taken over: the slower, exact path
rig = build(keyed=False)
action = bpy.data.actions.new("nla")
rig.animation_data_create()
track = rig.animation_data.nla_tracks.new()
pb = rig.pose.bones["h2"]
pb.rotation_mode = "XYZ"
pb.keyframe_insert("rotation_euler", frame=1)
nla_action = rig.animation_data.action
rig.animation_data.action = None
track.strips.new("nla", 1, nla_action)
rig.animation_data.action = bpy.data.actions.new("own")
scene.frame_set(1)
scene.swish.simulate = True
rt = live.runtime(scene)
check("a chain channel an NLA strip animates keeps the exact path", not rt.fast and rt.needs_post_replay())
scene.swish.simulate = False

# --- the lean bake: same result, and every object comes back
rig = build()
extra = bpy.data.objects.new("mesh", bpy.data.meshes.new("mesh"))
scene.collection.objects.link(extra)
scene.frame_set(1)
scene.swish.simulate = True
rt = live.runtime(scene)
needed = live._essentials(scene, rt)
check("the bake evaluates the rig and skips unrelated meshes", "rig" in needed and "mesh" not in needed)
bpy.ops.swish.cache_all()
check("Cache All fills the range", live.runtime(scene).cached_range() == (1, 40))
check("... and unhides what it hid", not extra.hide_viewport)
lean = {f: live.runtime(scene).cache[f].channels[0]["rotation_euler"].copy() for f in (10, 30)}
with_mesh = live.lean_evaluation
live.lean_evaluation = type("NoLean", (), {"__init__": lambda self, *a: None, "__enter__": lambda self: self,
                                           "__exit__": lambda self, *a: False})
bpy.ops.swish.cache_all()
live.lean_evaluation = with_mesh
full = {f: live.runtime(scene).cache[f].channels[0]["rotation_euler"] for f in (10, 30)}
check("... with the same result as evaluating everything", all(np.array_equal(lean[f], full[f]) for f in lean))
scene.swish.simulate = False

swish.unregister()
finish()
