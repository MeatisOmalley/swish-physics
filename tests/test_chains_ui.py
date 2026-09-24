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
check("clicking a group in another armature makes it the active object and group",
      bpy.ops.swish.group_activate(armature="other", index=0) == {"FINISHED"}
      and bpy.context.view_layer.objects.active == other)
bpy.ops.swish.group_activate(armature="skirt", index=0)
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

# --- stiffness as settle time
g = skirt_rig.swish.groups[0]
g.stiffness = 0.05
check("Kawaii's default stiffness settles 95% of the way in about a second",
      abs(g.settle_time - 0.9737) < 1e-3, g.settle_time)
g.settle_time = 3.0
check("setting a settle time sets Kawaii's stiffness", 0.0 < g.stiffness < 0.05
      and abs(g.settle_time - 3.0) < 1e-3, (g.stiffness, g.settle_time))
g.settle_time = 1000.0
check("the longest settle time turns stiffness off", g.stiffness == 0.0)
serialize = sys.modules["swish_physics.data.serialize"]
check("saved setups keep Kawaii's stiffness, not the settle time",
      "settle_time" not in serialize.settings_to_dict(g) and "stiffness" in serialize.settings_to_dict(g))

# --- the simulation still runs over the reshaped groups
scene.frame_set(1)
scene.swish.simulate = True
for f in range(2, 12):
    scene.frame_set(f)
live = sys.modules["swish_physics.runtime.live"]
check("the reshaped groups simulate", live.runtime(scene).system.n > 0)
scene.swish.simulate = False

swish.unregister()
finish()
