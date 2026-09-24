"""Mid-simulation edits: armatures renamed, moved, hidden, duplicated and deleted; bones renamed and deleted.
Nothing errors, the runtime rebuilds from what is there, and groups follow renamed bones."""
import io
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import check, finish, fresh_import

import bpy
from mathutils import Vector

addon = fresh_import()
addon.register()
from waifu_physics.data import bone_refs, colliders

live = sys.modules["waifu_physics.runtime.live"]
scene = bpy.context.scene


def armature(name, panels=4, stacked=False):
    data = bpy.data.armatures.new(name)
    obj = bpy.data.objects.new(name, data)
    scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    hips = data.edit_bones.new("hips")
    hips.head, hips.tail = (0, 0, 1.0), (0, 0, 1.1)
    thigh = data.edit_bones.new("thigh")
    thigh.head, thigh.tail, thigh.parent = (0.1, 0, 0.9), (0.1, 0, 0.5), hips
    for k in range(panels):
        a = 2 * math.pi * k / panels
        for layer in (("", "b") if stacked else ("",)):
            parent, head = hips, Vector((0.15 * math.cos(a), 0.15 * math.sin(a), 1.0))
            for i in range(3):
                bone = data.edit_bones.new(f"p{k}{layer}_{i}")
                bone.head, bone.tail, bone.parent = head, head + Vector((0, 0, -0.1)), parent
                parent, head = bone, bone.tail.copy()
    bpy.ops.object.mode_set(mode="OBJECT")
    return obj


rig = armature("rig")
body = armature("body", panels=0)
group = rig.waifu_physics.groups.add()
group.name = "Skirt"
for k in range(4):
    group.roots.add().name = f"p{k}_0"
group.excluded.add().name = "p3_2"
link = group.links.add()
link.bone_a, link.bone_b = "p0_1", "p1_1"
force = group.forces.add()
force.apply_bones.add().name = "p2_1"
force.ignore_bones.add().name = "p1_2"
sync = group.sync_bones.add()
sync.bone = "thigh"
target = sync.targets.add()
target.bone = "p0_0"
group.collider_sets.add().armature = body
colliders.add(body, "hips", "Sphere")
for frame in range(1, 60):
    rig.location.x = 0.05 * math.sin(frame / 3)
    rig.keyframe_insert("location", index=0, frame=frame)

# Handler errors are printed, not raised: catch them on stderr.
caught = io.StringIO()
real_stderr = sys.stderr


def frames(count=3):
    """Edit as the UI does (its depsgraph update runs), then play on."""
    sys.stderr = caught
    try:
        bpy.context.view_layer.update()
        for _ in range(count):
            scene.frame_set(scene.frame_current + 1)
    finally:
        sys.stderr = real_stderr


def points():
    current = live._runtimes.get(scene.as_pointer())
    return current.system.n if current is not None else 0


def roots():
    return [item.name for item in group.roots]


def clean(label):
    text = caught.getvalue()
    caught.seek(0)
    caught.truncate()
    check(label + ": no errors", "Error" not in text and "Traceback" not in text, text[-600:])


# --- setting a bone name remembers where the bone is, however it is set (here, a script)
check("the bones the groups name are remembered as they are named",
      {item.name for item in rig.waifu_physics.known_bones} >= {"p0_0", "p0_1", "p1_1", "thigh", "p3_2"})

scene.frame_set(1)
scene.waifu_physics.simulate = True
frames()
start = points()
check("it simulates", start > 0)
clean("start")

# --- the armature object: renamed, moved, hidden
rig.name = "renamed rig"
frames()
check("renaming the armature changes nothing", points() == start)
clean("armature renamed")
other = bpy.data.collections.new("Other")
scene.collection.children.link(other)
other.objects.link(rig)
scene.collection.objects.unlink(rig)
frames()
check("moving it to another collection changes nothing", points() == start)
layer = bpy.context.view_layer.layer_collection.children["Other"]
layer.exclude = True
frames()
layer.exclude = False
frames()
check("excluding its collection and back", points() == start)
rig.hide_set(True)
frames()
rig.hide_set(False)
rig.hide_viewport = True
frames()
rig.hide_viewport = False
frames()
check("hiding and disabling it", points() == start)
body.name = "renamed body"
frames()
check("renaming the collider armature changes nothing", points() == start
      and group.collider_sets[0].armature == body)
clean("armature moved and hidden")

# --- bones renamed: every reference follows
rig.data.bones["p0_0"].name = "front_0"
frames()
check("a root renamed in Object Mode: the group follows", roots() == ["front_0", "p1_0", "p2_0", "p3_0"], roots())
check("... and so does the sync target naming it", target.bone == "front_0")
check("... and the chain still simulates", points() == start)
bpy.context.view_layer.objects.active = rig
bpy.ops.object.mode_set(mode="EDIT")
rig.data.edit_bones["p1_1"].name = "side_1"
rig.data.edit_bones["thigh"].name = "leg"
rig.data.edit_bones["p1_2"].name = "side_2"
bpy.ops.object.mode_set(mode="OBJECT")
frames()
check("bones renamed in Edit Mode: links, sync bones and force filters follow",
      (link.bone_a, link.bone_b, sync.bone, force.ignore_bones[0].name) == ("p0_1", "side_1", "leg", "side_2"),
      (link.bone_a, link.bone_b, sync.bone, force.ignore_bones[0].name))
check("... and nothing is reported missing", bone_refs.missing(rig) == [])
clean("bones renamed")

# --- a bone deleted: the runtime rebuilds without it, and the panel can clean up
bpy.ops.object.mode_set(mode="EDIT")
rig.data.edit_bones.remove(rig.data.edit_bones["p2_0"])
bpy.ops.object.mode_set(mode="OBJECT")
frames()
check("deleting a chain's root: the runtime rebuilds without it", 0 < points() < start, points())
check("... and the missing bone is reported", bone_refs.missing(rig) == ["p2_0"], bone_refs.missing(rig))
bpy.context.view_layer.objects.active = rig
check("Clean Up removes what named it", bpy.ops.waifu_physics.bones_clean_up() == {"FINISHED"}
      and "p2_0" not in roots() and bone_refs.missing(rig) == [])
clean("bone deleted")
after_delete = points()

# --- armatures duplicated and deleted
copy = rig.copy()
copy.data = rig.data.copy()
scene.collection.objects.link(copy)
frames()
check("a duplicated armature joins the simulation", points() == 2 * after_delete, points())
bpy.data.objects.remove(copy)
frames()
check("... and leaves it when deleted", points() == after_delete, points())
group = link = force = sync = target = None          # the armature's data goes with it
bpy.data.objects.remove(rig)
frames()
check("deleting the simulated armature: no crash, nothing left simulating", points() == 0)
clean("armatures duplicated and deleted")

# --- stacked duplicate chains (as VRoid exports skirt layers): the parent tells them apart
stack = armature("stack", panels=1, stacked=True)
held = stack.waifu_physics.groups.add()
held.roots.add().name = "p0b_1"
held.excluded.add().name = "p0_1"
stack.data.bones["p0b_1"].name = "layer_1"
bpy.context.view_layer.update()
check("a renamed bone stacked on a twin: the one under the same parent is followed",
      held.roots[0].name == "layer_1", held.roots[0].name)
same_parent = armature("twins", panels=0)
bpy.context.view_layer.objects.active = same_parent
bpy.ops.object.mode_set(mode="EDIT")
for name in ("a", "b"):
    bone = same_parent.data.edit_bones.new(name)
    bone.head, bone.tail, bone.parent = (0, 0, 0.5), (0, 0, 0.3), same_parent.data.edit_bones["hips"]
bpy.ops.object.mode_set(mode="OBJECT")
twin_group = same_parent.waifu_physics.groups.add()
twin_group.roots.add().name = "a"
same_parent.data.bones["a"].name = "c"
bpy.context.view_layer.update()
check("twins nothing tells apart are not guessed at: the name is reported missing",
      twin_group.roots[0].name == "a" and bone_refs.missing(same_parent) == ["a"])

addon.unregister()
finish()
