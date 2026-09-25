"""Link Chains: neighbours by angle, loops and strips, and links that hold a skirt together."""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import check, finish, fresh_import

import bpy
import numpy as np
from mathutils import Vector

addon = fresh_import()
addon.register()
live = sys.modules["waifu_physics.runtime.live"]
draw = sys.modules["waifu_physics.ui.draw"]
scene = bpy.context.scene
PANELS = 6


def skirt(name):
    """Six panels of four bones hanging from a ring, panel k at angle 60k degrees."""
    data = bpy.data.armatures.new(name)
    obj = bpy.data.objects.new(name, data)
    scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    hips = data.edit_bones.new("hips")
    hips.head, hips.tail = (0, 0, 1.0), (0, 0, 1.1)
    for k in range(PANELS):
        angle = 2 * math.pi * k / PANELS
        parent, head = hips, Vector((0.2 * math.cos(angle), 0.2 * math.sin(angle), 1.0))
        for i in range(4):
            bone = data.edit_bones.new(f"panel{k}_{i}")
            bone.head = head
            bone.tail = head + Vector((0.03 * math.cos(angle), 0.03 * math.sin(angle), -0.12))
            bone.parent = parent
            parent, head = bone, bone.tail.copy()
    bpy.ops.object.mode_set(mode="OBJECT")
    group = obj.waifu_physics.groups.add()
    for k in range(PANELS):
        group.roots.add().name = f"panel{k}_0"
    group.dummy_bone_length = 0.05
    return obj, group


def link(obj, mode, selection_order):
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="POSE")
    for pb in obj.pose.bones:
        pb.select = False
    for k in selection_order:
        obj.pose.bones[f"panel{k}_2"].select = True        # any bone of a chain selects the chain
    result = bpy.ops.waifu_physics.link_chains(loop=mode == "LOOP")
    bpy.ops.object.mode_set(mode="OBJECT")
    return result


obj, group = skirt("loop")
check("Link as Loop runs", link(obj, "LOOP", [3, 0, 5, 1, 4, 2]) == {"FINISHED"})
panel = lambda name: int(name[5:name.index("_")])
depth = lambda name: int(name[name.index("_") + 1:])
pairs = {(min(panel(l.bone_a), panel(l.bone_b)), max(panel(l.bone_a), panel(l.bone_b))) for l in group.links}
expected = {(k, (k + 1) % PANELS) if k < (k + 1) % PANELS else ((k + 1) % PANELS, k) for k in range(PANELS)}
check("a loop links each panel to its neighbours by angle, whatever the selection order", pairs == expected,
      sorted(pairs))
check("... at every depth below the roots, bone by bone", len(group.links) == PANELS * 3 and
      all(depth(l.bone_a) == depth(l.bone_b) and depth(l.bone_a) >= 1 for l in group.links), len(group.links))
check("linking again adds nothing new", link(obj, "LOOP", range(PANELS)) == {"FINISHED"} and len(group.links) == 18)

strip, strip_group = skirt("strip")
link(strip, "STRIP", [0, 1, 2, 3, 4, 5])
check("a strip leaves the ends open", len(strip_group.links) == (PANELS - 1) * 3, len(strip_group.links))
bpy.data.objects.remove(strip)

# --- links hold the panels' spacing where unlinked panels splay apart
spread = {}
for linked in (False, True):
    for other in list(bpy.data.objects):
        bpy.data.objects.remove(other)
    obj, group = skirt("run")
    if linked:
        link(obj, "LOOP", range(PANELS))
    group.gravity = (3.0, 0.0, -1.0)
    group.stiffness = 0.02
    group.compliance = "CONCRETE"
    scene.frame_set(1)
    scene.waifu_physics.simulate = True
    rest = np.array([obj.pose.bones[f"panel{k}_3"].head for k in range(PANELS)])
    for frame in range(1, 60):
        scene.frame_set(frame)
    now = np.array([obj.pose.bones[f"panel{k}_3"].head for k in range(PANELS)])
    gaps = lambda p: np.linalg.norm(p - np.roll(p, -1, axis=0), axis=1)
    spread[linked] = np.abs(gaps(now) - gaps(rest)).max()
    if linked:
        rt = live.runtime(scene)
        user_links = len(group.links)
        solver_links = len(rt.system.link_a)
    scene.waifu_physics.simulate = False
check("linked panels keep their spacing far better than unlinked ones", spread[True] < spread[False] * 0.25, spread)
check("the solver gets the links plus automatic links between the panels' tips",
      solver_links == user_links + PANELS, (user_links, solver_links))

group.bridge_count = 1
scene.waifu_physics.simulate = True
scene.frame_set(2)
bridges = int((live.runtime(scene).system.kind == 3).sum())
check("bridge points appear along the links", bridges == solver_links, (bridges, solver_links))
scene.waifu_physics.simulate = False

check("the link overlay is drawing", draw._handle is not None)
# --- a strip orders its chains along their row: a flat cape links neighbours, never its two edges
from waifu_physics.data import links as chain_links
cape_data = bpy.data.armatures.new("cape")
cape = bpy.data.objects.new("cape", cape_data)
bpy.context.scene.collection.objects.link(cape)
bpy.context.view_layer.objects.active = cape
bpy.ops.object.mode_set(mode="EDIT")
back = cape_data.edit_bones.new("back")
back.head, back.tail = (0, 0, 1.5), (0, 0, 1.6)
cape_roots = []
for i, x in enumerate((0.1, -0.2, 0.2, -0.1, 0.0)):        # made out of order, in a straight row
    parent, head = back, Vector((x, 0.1, 1.5))
    for depth in range(3):
        bone = cape_data.edit_bones.new(f"cape{i}_{depth}")
        bone.head, bone.tail = head, head + Vector((0, 0, -0.15))
        bone.parent = parent
        parent, head = bone, bone.tail.copy()
    cape_roots.append(f"cape{i}_0")
bpy.ops.object.mode_set(mode="OBJECT")
row = [pair for pair in chain_links.pairs(cape, cape_roots, loop=False) if pair[0].endswith("_1")]
x_of = {f"cape{i}_1": x for i, x in enumerate((0.1, -0.2, 0.2, -0.1, 0.0))}
steps = sorted(round(abs(x_of[a] - x_of[b]), 3) for a, b in row)
check("a strip links each chain of a flat row to its neighbour, and not its two edges together",
      len(row) == 4 and steps == [0.1] * 4, row)

# --- Link Selected Bones links only the bones selected
def link_bones(obj, names, loop=False):
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="POSE")
    for pb in obj.pose.bones:
        pb.select = pb.name in names
    try:
        result = bpy.ops.waifu_physics.link_bones(loop=loop)
    except RuntimeError:
        result = {"CANCELLED"}
    bpy.ops.object.mode_set(mode="OBJECT")
    return result


obj, group = skirt("single")
check("two selected bones make exactly one link, not their whole chains",
      link_bones(obj, ["panel0_2", "panel1_2"]) == {"FINISHED"} and len(group.links) == 1
      and {group.links[0].bone_a, group.links[0].bone_b} == {"panel0_2", "panel1_2"},
      [(l.bone_a, l.bone_b) for l in group.links])
group.links.clear()
check("three bones with Close the Loop make three links, round the ring",
      link_bones(obj, ["panel0_1", "panel2_1", "panel4_1"], loop=True) == {"FINISHED"} and len(group.links) == 3)
group.links.clear()
check("... and without it two, leaving the ends open",
      link_bones(obj, ["panel0_1", "panel2_1", "panel4_1"]) == {"FINISHED"} and len(group.links) == 2)
group.links.clear()
check("a bone outside the group's chains is not linked",
      link_bones(obj, ["panel0_1", "hips"]) == {"CANCELLED"} and len(group.links) == 0)

addon.unregister()
check("... and stops when unregistered", draw._handle is None)
finish()
