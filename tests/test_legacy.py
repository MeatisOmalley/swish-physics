"""Files saved while the add-on was called Swish Physics: on load, their groups, scene settings, curves and
colliders come back under the new names."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import check, finish, fresh_import

import bpy

addon = fresh_import()
addon.register()
from waifu_physics.data import colliders, curves, legacy, serialize

scene = bpy.context.scene

data = bpy.data.armatures.new("rig")
rig = bpy.data.objects.new("rig", data)
scene.collection.objects.link(rig)
bpy.context.view_layer.objects.active = rig
bpy.ops.object.mode_set(mode="EDIT")
parent = None
for i in range(4):
    bone = data.edit_bones.new(f"b{i}")
    bone.head, bone.tail = (0, 0, 1.0 - 0.1 * i), (0, 0, 0.9 - 0.1 * i)
    bone.parent = parent
    parent = bone
bpy.ops.object.mode_set(mode="OBJECT")

group = rig.waifu_physics.groups.add()
group.name = "Hair"
group.roots.add().name = "b1"
group.excluded.add().name = "b3"
link = group.links.add()
link.bone_a, link.bone_b = "b1", "b2"
group.damping_level, group.stiffness_level, group.radius = 6.5, 4.0, 0.02
group.use_radius_curve = True
curves.node(group, "radius").mapping.curves[0].points.new(0.5, 0.25)
group.forces.add()
second = rig.waifu_physics.groups.add()
second.name = "Other"
second.roots.add().name = "b0"
rig.waifu_physics.active_group = 1
scene.waifu_physics.target_framerate = 90
collider = colliders.add(rig, "b0", "Capsule")

before = [serialize.group_to_dict(g) for g in rig.waifu_physics.groups]
collider_before = serialize.collider_to_dict(collider)

# Make it an old file: the same data under the old names, as Swish Physics saved it.
for block, old, new in ((rig, "swish", "waifu_physics"), (scene, "swish", "waifu_physics"),
                        (collider, "swish_collider", "waifu_physics_collider")):
    stored = block.bl_system_properties_get()
    stored[old] = stored[new].to_dict()
    del stored[new]
bpy.data.node_groups[curves.HOST].name = ".Swish Curves"
bpy.data.node_groups[colliders.TREE].name = ".Swish Collider"
bpy.data.collections[colliders.COLLECTION].name = "Swish Colliders"
collider.modifiers[colliders.MODIFIER].name = "Swish Collider"
stored = rig.bl_system_properties_get()
check("the old file holds no new-named data", "waifu_physics" not in stored and "swish" in stored)

path = os.path.join(tempfile.mkdtemp(), "old.blend")
bpy.ops.wm.save_as_mainfile(filepath=path)
bpy.ops.wm.open_mainfile(filepath=path)          # load_post runs the migration

rig = bpy.data.objects["rig"]
collider = bpy.data.objects[collider_before["name"]]
scene = bpy.context.scene
after = [serialize.group_to_dict(g) for g in rig.waifu_physics.groups]
check("the groups come back whole: chains, exclusions, links, settings, curves and forces", after == before,
      (before, after))
check("... and which group was active", rig.waifu_physics.active_group == 1)
check("the scene's settings come back", scene.waifu_physics.target_framerate == 90)
check("the old properties are gone", "swish" not in rig.bl_system_properties_get()
      and "swish" not in scene.bl_system_properties_get())
check("the curves node group has the new name", curves.HOST in bpy.data.node_groups
      and ".Swish Curves" not in bpy.data.node_groups)
check("the collider comes back, its modifier, tree and collection renamed",
      serialize.collider_to_dict(collider) == collider_before and colliders.MODIFIER in collider.modifiers
      and colliders.COLLECTION in bpy.data.collections and colliders.TREE in bpy.data.node_groups)
check("migrating again moves nothing", legacy.migrate() == 0)
check("a setup copied before the rename still pastes",
      serialize.check({"format": "swish_physics", "version": 1}) is None)

addon.unregister()
finish()
