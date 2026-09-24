"""The group tree and the Chains tab: which armatures are listed, and splitting, moving, merging and
removing chains."""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import check, finish, fresh_import

import bpy
from mathutils import Vector

swish = fresh_import()
swish.register()
panels = sys.modules["swish_physics.ui.panels"]
scene = bpy.context.scene


def skirt(name, panels_count=6, grouped=True):
    data = bpy.data.armatures.new(name)
    obj = bpy.data.objects.new(name, data)
    scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    hips = data.edit_bones.new("hips")
    hips.head, hips.tail = (0, 0, 1.0), (0, 0, 1.1)
    for k in range(panels_count):
        angle = 2 * math.pi * k / panels_count
        parent, head = hips, Vector((0.2 * math.cos(angle), 0.2 * math.sin(angle), 1.0))
        for i in range(3):
            bone = data.edit_bones.new(f"p{k}_{i}")
            bone.head, bone.tail = head, head + Vector((0, 0, -0.12))
            bone.parent = parent
            parent, head = bone, bone.tail.copy()
    bpy.ops.object.mode_set(mode="OBJECT")
    if grouped:
        group = obj.swish.groups.add()
        group.name = "Skirt"
        for k in range(panels_count):
            group.roots.add().name = f"p{k}_0"
        group.damping = 0.37
    return obj


skirt_rig = skirt("skirt")
bare = skirt("bare", grouped=False)
other = skirt("other", panels_count=2)

# --- which armatures the tree lists
for obj in scene.objects:
    obj.select_set(False)
bpy.context.view_layer.objects.active = skirt_rig
skirt_rig.select_set(True)
scene.swish.selected_only = True
check("with Selected Only, the tree lists the selected armature", panels._tree_armatures(bpy.context) == [skirt_rig])
scene.swish.selected_only = False
listed = panels._tree_armatures(bpy.context)
check("without it, every armature with a group, and not one without",
      set(listed) == {skirt_rig, other} and bare not in listed, [o.name for o in listed])
other.swish.active_group = 0                       # a click in the other armature's list
check("picking a group in another armature's list makes that armature active",
      bpy.context.view_layer.objects.active == other)
skirt_rig.swish.active_group = 0
group = skirt_rig.swish.groups[0]

# --- the reported case: Selected Only off, click an armature with no group, give it one
scene.swish.selected_only = False
bpy.context.view_layer.objects.active = skirt_rig
bpy.ops.object.mode_set(mode="POSE")
bpy.ops.swish.armature_activate(armature="bare")        # clicking its name, or clicking it in the viewport
check("an armature with no group is listed once it is the active one",
      bare in panels._tree_armatures(bpy.context) and bpy.context.view_layer.objects.active == bare)
check("... and it is in Pose Mode, as the user was", bare.mode == "POSE")
for pb in bare.pose.bones:
    pb.select = False
try:
    empty = bpy.ops.swish.group_new()
except RuntimeError:
    empty = {"CANCELLED"}
check("+ with nothing selected says what to do instead of doing nothing", empty == {"CANCELLED"})
for pb in bare.pose.bones:
    pb.select = pb.name in ("p0_0", "p1_0")
check("+ gives it its first group", bpy.ops.swish.group_new() == {"FINISHED"} and len(bare.swish.groups) == 1
      and [r.name for r in bare.swish.groups[0].roots] == ["p0_0", "p1_0"])
bpy.ops.object.mode_set(mode="OBJECT")
bpy.ops.swish.armature_activate(armature="skirt")
group = skirt_rig.swish.groups[0]

# --- links around the skirt, then split two chains off
bpy.ops.object.mode_set(mode="POSE")
for pb in skirt_rig.pose.bones:
    pb.select = pb.name.endswith("_1")
bpy.ops.swish.link_chains(mode="LOOP")
links_before = len(group.links)
bpy.ops.swish.chain_click(root="p0_0")
bpy.ops.swish.chain_click(root="p1_0", extend=True)
ops = sys.modules["swish_physics.ui.ops"]
check("clicking a chain row selects it; Shift-click adds another",
      ops.selected_chains(skirt_rig, group) == ["p0_0", "p1_0"]
      and {pb.name for pb in skirt_rig.pose.bones if pb.select} == {f"p{k}_{i}" for k in (0, 1) for i in range(3)})
bpy.ops.swish.chain_click(root="p1_0", extend=True)
check("Shift-clicking a selected chain drops it", ops.selected_chains(skirt_rig, group) == ["p0_0"])
bpy.ops.swish.chain_click(root="p0_0")
bpy.ops.swish.chain_click(root="p3_0", span=True)
check("Ctrl-click selects the range between", ops.selected_chains(skirt_rig, group) == ["p0_0", "p1_0", "p2_0", "p3_0"])
bpy.ops.swish.chain_click(root="p0_0")
bpy.ops.swish.chain_click(root="p1_0", extend=True)
check("Split to New Group runs", bpy.ops.swish.chains_split() == {"FINISHED"})
split = skirt_rig.swish.groups[1]
check("the split group holds the selected chains, the rest stay",
      [r.name for r in split.roots] == ["p0_0", "p1_0"] and len(group.roots) == 4)
check("... with the original's settings", math.isclose(split.damping, 0.37, rel_tol=1e-6))
names = lambda g: {frozenset((l.bone_a, l.bone_b)) for l in g.links}
inside = {frozenset(("p0_1", "p1_1")), frozenset(("p0_2", "p1_2"))}
check("... links inside the split chains move with them", names(split) == inside, names(split))
check("... and links crossing to the chains left behind are removed",
      len(group.links) == links_before - len(inside) - 4, (links_before, len(group.links)))

# --- move chains back by viewport selection: moving all of them merges the groups
skirt_rig.swish.active_group = 1
for pb in skirt_rig.pose.bones:
    pb.select = pb.name.startswith(("p0_", "p1_"))       # as a box select in the viewport would
check("the move-here arrow moves the chains holding selected bones",
      bpy.ops.swish.chains_move_here(armature="skirt", index=0) == {"FINISHED"})
check("... and moving them all merges the groups: the empty one is gone",
      len(skirt_rig.swish.groups) == 1 and len(skirt_rig.swish.groups[0].roots) == 6)
group = skirt_rig.swish.groups[0]
check("... the moved chains' links came along", inside <= names(group))

# --- select in viewport, remove
bpy.ops.swish.chain_click(root="p5_0")
check("clicking a row selects just that chain's bones",
      {pb.name for pb in skirt_rig.pose.bones if pb.select} == {"p5_0", "p5_1", "p5_2"})
check("Remove Chains stops simulating them", bpy.ops.swish.chains_remove() == {"FINISHED"}
      and "p5_0" not in [r.name for r in group.roots] and len(group.roots) == 5)
bpy.ops.object.mode_set(mode="OBJECT")

# --- stiffness 0-10, inertia, gravity: shown the intuitive way round, Kawaii's values stored
g = skirt_rig.swish.groups[0]
g.stiffness = 0.05
check("Kawaii's default stiffness shows as 9.03 of 10 (10 minus its 0.97 s to settle)",
      abs(g.stiffness_level - 9.0266) < 1e-3, g.stiffness_level)
g.stiffness_level = 7.0
check("setting 7 means settling in 3 s", 0.0 < g.stiffness < 0.05 and abs(g.stiffness_level - 7.0) < 1e-3,
      (g.stiffness, g.stiffness_level))
g.stiffness_level = 0.0
check("0 is the loosest: ten seconds to settle, still some pull", 0.0 < g.stiffness < 0.01, g.stiffness)
g.stiffness_level = 10.0
check("10 snaps straight back", g.stiffness > 0.99, g.stiffness)
g.world_damping_location = 0.8
check("World Damping 0.8 shows as Movement Inertia 0.2", abs(g.movement_inertia - 0.2) < 1e-6)
g.turning_inertia = 1.0
check("Turning Inertia 1 is World Damping Rotation 0", g.world_damping_rotation == 0.0)
serialize = sys.modules["swish_physics.data.serialize"]
saved = serialize.settings_to_dict(g)
check("saved setups keep Kawaii's values, not the display ones",
      not {"stiffness_level", "movement_inertia", "turning_inertia"} & set(saved) and "stiffness" in saved)
g.stiffness = 0.05

# --- the right-click Move Chains to Group: from any groups, into one, or a new one
bpy.context.view_layer.objects.active = skirt_rig
bpy.ops.object.mode_set(mode="POSE")
for pb in skirt_rig.pose.bones:
    pb.select = pb.name.startswith(("p0_", "p4_"))
before = len(skirt_rig.swish.groups)
check("Move Chains to Group > New Group makes a group of them",
      bpy.ops.swish.chains_to_group(index=-1) == {"FINISHED"} and len(skirt_rig.swish.groups) == before + 1
      and sorted(r.name for r in skirt_rig.swish.groups[-1].roots) == ["p0_0", "p4_0"])
for pb in skirt_rig.pose.bones:
    pb.select = pb.name.startswith(("p0_", "p1_"))
check("... and moving chains from two groups into one works in one go",
      bpy.ops.swish.chains_to_group(index=0) == {"FINISHED"}
      and {"p0_0", "p1_0"} <= {r.name for r in skirt_rig.swish.groups[0].roots})
check("the menu is in the Pose Mode right-click menu",
      any(getattr(f, "__name__", "") == "_pose_menu" for f in bpy.types.VIEW3D_MT_pose_context_menu._dyn_ui_initialize()))
bpy.ops.object.mode_set(mode="OBJECT")

# --- the simulation still runs over the reshaped groups
scene.frame_set(1)
scene.swish.simulate = True
for f in range(2, 12):
    scene.frame_set(f)
live = sys.modules["swish_physics.runtime.live"]
check("the reshaped groups simulate", live.runtime(scene).system.n > 0)
import numpy as np
skirt_rig.swish.groups[0].gravity_scale = 2.0
scene.frame_set(12)
rt = live.runtime(scene)
g_index = next(i for i, (r, k) in enumerate(rt.group_props) if rt.rigs[r].obj == skirt_rig and k == 0)
check("Gravity Scale 2 pulls with twice the scene's gravity",
      np.allclose(rt.system.gravity[g_index], np.array(scene.gravity) * 2.0 * rt.cm, atol=1e-4),
      rt.system.gravity[g_index])
scene.swish.simulate = False

swish.unregister()
finish()
