"""The group tree and the chain manager: which armatures are listed, and selecting, moving, merging and
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
for obj in scene.objects:
    obj.select_set(True)                                  # Select All
check("... every selected armature, with or without groups",
      set(panels._tree_armatures(bpy.context)) == {skirt_rig, bare, other}
      and panels._tree_armatures(bpy.context)[0] == skirt_rig)
for obj in scene.objects:
    obj.select_set(False)                                 # click off: the active object stays active
check("... and none once nothing is selected, though one is still the active object",
      panels._tree_armatures(bpy.context) == [] and bpy.context.view_layer.objects.active == skirt_rig)
skirt_rig.select_set(True)
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

# --- links around the skirt; select chains as the manager's clicks do
bpy.ops.object.mode_set(mode="POSE")
for pb in skirt_rig.pose.bones:
    pb.select = pb.name.endswith("_1")
bpy.ops.swish.link_chains(mode="LOOP")
links_before = len(group.links)
ops = sys.modules["swish_physics.ui.ops"]
chosen = lambda: sorted(root for _g, root in ops.selected_chain_keys(skirt_rig))
bpy.ops.swish.chain_click(group=0, root="p0_0")
bpy.ops.swish.chain_click(group=0, root="p1_0", extend=True)
check("clicking a chain selects it; Ctrl-click adds another",
      chosen() == ["p0_0", "p1_0"]
      and {pb.name for pb in skirt_rig.pose.bones if pb.select} == {f"p{k}_{i}" for k in (0, 1) for i in range(3)})
bpy.ops.swish.chain_click(group=0, root="p1_0", extend=True)
check("Ctrl-clicking a selected chain drops it", chosen() == ["p0_0"])
bpy.ops.swish.chain_click(group=0, root="p0_0")
bpy.ops.swish.chain_click(group=0, root="p3_0", span=True)
check("Shift-click selects the range between", chosen() == ["p0_0", "p1_0", "p2_0", "p3_0"])
bpy.ops.swish.chains_select(action="NONE")
check("clicking empty space selects none", chosen() == [])
bpy.ops.swish.chains_select(action="ALL")
check("A selects them all", len(chosen()) == 6)

# --- New Group from the selection
bpy.ops.swish.chain_click(group=0, root="p0_0")
bpy.ops.swish.chain_click(group=0, root="p1_0", extend=True)
check("New Group runs", bpy.ops.swish.chains_to_group(index=-1) == {"FINISHED"})
split = skirt_rig.swish.groups[1]
check("the new group holds the selected chains, the rest stay",
      [r.name for r in split.roots] == ["p0_0", "p1_0"] and len(group.roots) == 4)
check("... named from them, not a bone name", split.name == "p0" or not split.name.startswith("J_"), split.name)
check("... with the original's settings", math.isclose(split.damping, 0.37, rel_tol=1e-6))
names = lambda g: {frozenset((l.bone_a, l.bone_b)) for l in g.links}
inside = {frozenset(("p0_1", "p1_1")), frozenset(("p0_2", "p1_2"))}
check("... links inside the moved chains move with them", names(split) == inside, names(split))
check("... and links crossing to the chains left behind are removed",
      len(group.links) == links_before - len(inside) - 4, (links_before, len(group.links)))

# --- the manager's layout: folders, rows, what a click or a drop lands on
manager = sys.modules["swish_physics.ui.manager"]
layout = manager.Layout(skirt_rig, (1200, 900), 1.0, place=(20, 800))
kinds = [(item.kind, item.group, item.root) for item in layout.rows]
check("the manager lists each group as a folder, its chains under it",
      kinds == [("group", 0, "")] + [("chain", 0, f"p{k}_0") for k in (2, 3, 4, 5)]
      + [("group", 1, "")] + [("chain", 1, f"p{k}_0") for k in (0, 1)], kinds)
row = layout.rows[2]
mid_y = (row.y0 + row.y1) / 2
check("a click on a row hits that chain", layout.hit(row.x0 + 60, mid_y).root == "p3_0")
check("... and its right end is the edge that widens the manager", layout.hit(row.x1 - 2, mid_y).kind == "edge_right")
check("its bottom edge makes it taller", layout.hit(row.x0 + 100, layout.frame.y0 + 2).kind == "edge_bottom")
check("a group's arrow folds it", layout.hit(layout.rows[0].x0 + 8, (layout.rows[0].y0 + layout.rows[0].y1) / 2).kind == "fold")
check("dropping on a chain row drops into its group", layout.drop_target(row.x0 + 60, mid_y) == ("group", 0))
check("dropping on the empty space under the rows makes a new group",
      layout.drop_target(layout.empty.x0 + 30, (layout.empty.y0 + layout.empty.y1) / 2) == ("new", -1))
check("outside the manager is nothing", layout.hit(layout.frame.x1 + 5, mid_y) is None)
tools = {item.kind: item.enabled for item in layout.items if item.kind in ("new", "merge", "delete")}
check("with chains of one group selected, New Group and Delete are on, Merge is off",
      tools == {"new": True, "merge": False, "delete": True}, tools)
skirt_rig.pose.bones["p2_1"].select = True
layout = manager.Layout(skirt_rig, (1200, 900), 1.0, place=(20, 800))
check("... and a chain from a second group turns Merge on",
      next(i for i in layout.items if i.kind == "merge").enabled)
skirt_rig.swish.groups[1].show_chains = False
layout = manager.Layout(skirt_rig, (1200, 900), 1.0, place=(20, 800))
check("a folded group hides its chains", len(layout.rows) == 6)
skirt_rig.swish.groups[1].show_chains = True
small = manager.Layout(skirt_rig, (1200, 180), 1.0, place=(20, 170), scroll=2)
resized = manager.Layout(skirt_rig, (1200, 900), 1.0, place=(20, 800), size=(360, 200))
check("dragged edges set the size", abs(resized.frame.x1 - resized.frame.x0 - 360) < 1e-6
      and abs(resized.frame.y1 - resized.frame.y0 - 200) < 1e-6)
check("... too short for the rows, it shows a scroll bar beside them",
      resized.overflow and resized.thumb is not None and resized.rows[0].x1 <= resized.track.x0)
check("... whose thumb, held, scrolls the rows", resized.hit((resized.thumb.x0 + resized.thumb.x1) / 2,
      (resized.thumb.y0 + resized.thumb.y1) / 2).kind == "scroll_thumb" and resized.rows_per_pixel > 0)
bottom = manager.Layout(skirt_rig, (1200, 900), 1.0, place=(20, 800), size=(360, 200), scroll=99)
check("... to the last row, with the thumb at the track's foot",
      bottom.first == bottom.total - bottom.capacity and abs(bottom.thumb.y0 - bottom.track.y0) < 1e-6)
check("a short viewport scrolls the rows", small.total == 8 and small.capacity < 8 and small.first == 2
      and small.rows[0].root == "p3_0", (small.total, small.capacity, small.first))
dragging = manager.Layout(skirt_rig, (1200, 900), 1.0, place=(20, 800), drag=manager.Drag("chains", 0, 2, 0, 0))
zone = dragging.rows[-1]
check("while chains are dragged, a new-group drop zone shows under the rows",
      zone.kind == "newzone" and dragging.drop_target(zone.x0 + 20, (zone.y0 + zone.y1) / 2) == ("new", -1))
check("the manager edits one armature: nothing on it names another",
      all(item.group < len(skirt_rig.swish.groups) for item in layout.items))

# --- merge: the button merges the selected chains' groups; dragging a folder onto another merges it
skirt_rig.swish.active_group = 0
check("Merge merges the groups of the selected chains into the active one",
      bpy.ops.swish.groups_merge() == {"FINISHED"} and len(skirt_rig.swish.groups) == 1
      and len(skirt_rig.swish.groups[0].roots) == 6 and skirt_rig.swish.groups[0].name == "Skirt")
group = skirt_rig.swish.groups[0]
check("... the moved chains' links came along", inside <= names(group))
bpy.ops.swish.chain_click(group=0, root="p4_0")
bpy.ops.swish.chains_to_group(index=-1)
check("a folder dragged onto another merges into it",
      bpy.ops.swish.groups_merge(source=1, target=0) == {"FINISHED"} and len(skirt_rig.swish.groups) == 1
      and len(group.roots) == 6)
bpy.ops.swish.group_click(index=0)
check("clicking a folder selects its chains and makes it the edited group",
      len(chosen()) == 6 and skirt_rig.swish.active_group == 0)

# --- delete: the selected chains go, and the next in line is selected
order = [r.name for r in group.roots]
doomed = order[2]
bpy.ops.swish.chain_click(group=0, root=doomed)
check("Delete stops simulating the selected chains", bpy.ops.swish.chains_remove() == {"FINISHED"}
      and doomed not in [r.name for r in group.roots] and len(group.roots) == 5)
check("... and selects the next chain in line", chosen() == [order[3]], (chosen(), order))
bpy.ops.swish.chain_click(group=0, root=order[-1])
bpy.ops.swish.chains_remove()
check("deleting the last chain selects the one before it", chosen() == [order[-2]], (chosen(), order))
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
    pb.select = pb.name.startswith(("p0_", "p3_"))
before = len(skirt_rig.swish.groups)
check("Move Chains to Group > New Group makes a group of them",
      bpy.ops.swish.chains_to_group(index=-1) == {"FINISHED"} and len(skirt_rig.swish.groups) == before + 1
      and sorted(r.name for r in skirt_rig.swish.groups[-1].roots) == ["p0_0", "p3_0"])
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
