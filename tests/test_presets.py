"""Presets: the name shown while a group still matches one, the user's own saved presets, and the 0-10
damping scale with its floor."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import check, finish, fresh_import

import bpy

swish = fresh_import()
swish.register()
presets = sys.modules["swish_physics.data.presets"]
curves = sys.modules["swish_physics.data.curves"]
props = sys.modules["swish_physics.data.props"]
scene = bpy.context.scene

temp = tempfile.mkdtemp()
presets.folder = lambda create=False: temp               # the user's presets, kept out of the real folder

data = bpy.data.armatures.new("rig")
obj = bpy.data.objects.new("rig", data)
scene.collection.objects.link(obj)
group = obj.swish.groups.add()

# --- the damping scale
check("stiffness comes before damping", curves.CURVED[:2] == ("stiffness", "damping"))
group.damping_level = 0.0
check("damping 0 is the floor, not zero", abs(group.damping - props.DAMPING_FLOOR) < 1e-6, group.damping)
group.damping_level = 5.0
check("damping 5 is Kawaii's default, 0.1", abs(group.damping - 0.1) < 1e-6, group.damping)
group.damping_level = 10.0
check("damping 10 is 0.33", abs(group.damping - 0.3333) < 1e-3, group.damping)
steps = []
for level in (2.0, 4.0, 6.0, 8.0):
    group.damping_level = level
    steps.append(group.damping)
ratios = [b / a for a, b in zip(steps, steps[1:])]
check("each step scales Kawaii's damping by the same factor", max(ratios) - min(ratios) < 1e-4, ratios)
group.damping = 0.0
check("a damping under the floor (an old file, an import) shows as 0", group.damping_level == 0.0)

# --- built-in presets: the name shows until a setting deviates
bpy.context.view_layer.objects.active = obj
obj.swish.active_group = 0
check("a fresh group matches no preset", presets.matching(group) is None, presets.matching(group))
presets.apply(group, "HAIR")
check("applying Hair names the group's preset Hair", presets.matching(group) == "Hair")
group.stiffness_level = 3.0
check("... until a setting deviates from it", presets.matching(group) is None)
presets.apply(group, "SKIRT")
check("every setting the Skirt preset names exists, so it can match", presets.matching(group) == "Skirt")
group.use_damping_curve = True
check("turning on a curve deviates too (presets turn curves off)", presets.matching(group) is None)
group.use_damping_curve = False
check("... and off again, it matches again", presets.matching(group) == "Skirt")
group.enabled = False
check("the group's on/off switch is not part of a preset", presets.matching(group) == "Skirt")
group.enabled = True

# --- the user's own presets
group.damping_level = 7.0
group.radius = 0.042
name = presets.save_user(group, "Long: Hair?")
check("a saved preset gets a file-safe name", name == "Long_ Hair_" and os.path.isfile(os.path.join(temp, name + ".json")),
      os.listdir(temp))
check("... it is listed", list(presets.user_presets()) == [name])
check("... and the group now shows it", presets.matching(group) == name)
presets.apply(group, "HAIR")
check("another preset replaces it", presets.matching(group) == "Hair")
presets.apply_user(group, name)
check("applying the saved preset brings its values back",
      abs(group.radius - 0.042) < 1e-6 and abs(group.damping_level - 7.0) < 1e-4 and presets.matching(group) == name)
group.enabled = False
presets.apply_user(group, name)
check("applying a preset leaves the group's on/off switch alone", group.enabled is False)
group.enabled = True
check("Delete removes it", presets.delete_user(name) and presets.user_presets() == {})
check("... and the group no longer matches it", presets.matching(group) is None)

# --- the operators and the menu
check("the preset menu is registered", hasattr(bpy.types, "SWISH_MT_presets"))
check("Apply Preset takes a built-in's key", bpy.ops.swish.preset_apply(preset="HAIR") == {"FINISHED"}
      and presets.matching(group) == "Hair")
check("... and refuses an unknown one", bpy.ops.swish.preset_apply(preset="NOPE") == {"CANCELLED"})
check("Save Preset refuses a built-in's name",
      bpy.ops.swish.preset_save("EXEC_DEFAULT", name="Hair") == {"CANCELLED"} and presets.user_presets() == {})
check("Save Preset saves the active group's settings",
      bpy.ops.swish.preset_save("EXEC_DEFAULT", name="Mine") == {"FINISHED"} and "Mine" in presets.user_presets())
check("Delete Preset deletes it", bpy.ops.swish.preset_delete("EXEC_DEFAULT", name="Mine") == {"FINISHED"}
      and presets.user_presets() == {})

swish.unregister()
finish()
