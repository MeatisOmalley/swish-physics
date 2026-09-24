"""The add-on registers, unregisters and survives Reload Scripts cleanly."""
import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy
from _harness import check, finish, fresh_import


def handler_counts():
    return {name: len(getattr(bpy.app.handlers, name)) for name in dir(bpy.app.handlers)
            if isinstance(getattr(bpy.app.handlers, name), list)}


handlers_before = handler_counts()
addon = fresh_import()
addon.register()
check("registering adds the Waifu Physics sidebar panel", hasattr(bpy.types, "WAIFU_PHYSICS_PT_main"))
panel = bpy.types.WAIFU_PHYSICS_PT_main
check("... in the 3D viewport's sidebar, under a Waifu Physics tab",
      panel.bl_space_type == "VIEW_3D" and panel.bl_region_type == "UI" and panel.bl_category == "Waifu Physics")

addon.unregister()
check("unregistering removes it", not hasattr(bpy.types, "WAIFU_PHYSICS_PT_main"))
check("... and leaves no app handlers behind", handler_counts() == handlers_before)

addon.register()
check("it registers again after unregistering", hasattr(bpy.types, "WAIFU_PHYSICS_PT_main"))

# Reload Scripts: unregister, reload the package in place, register again.
addon.unregister()
class_before = addon.panels.WAIFU_PHYSICS_PT_main
addon = importlib.reload(addon)
# A re-executed submodule defines its classes afresh.
check("reloading the package reloads its submodules", addon.panels.WAIFU_PHYSICS_PT_main is not class_before)
try:
    addon.register()
    check("it registers after a reload", hasattr(bpy.types, "WAIFU_PHYSICS_PT_main"))
except Exception as exc:
    check("it registers after a reload", False, exc)
addon.unregister()
check("... and unregisters after a reload, leaving no handlers", not hasattr(bpy.types, "WAIFU_PHYSICS_PT_main")
      and handler_counts() == handlers_before)

finish()
