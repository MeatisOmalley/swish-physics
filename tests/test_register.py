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
swish = fresh_import()
swish.register()
check("registering adds the Swish sidebar panel", hasattr(bpy.types, "SWISH_PT_main"))
panel = bpy.types.SWISH_PT_main
check("... in the 3D viewport's sidebar, under a Swish tab",
      panel.bl_space_type == "VIEW_3D" and panel.bl_region_type == "UI" and panel.bl_category == "Swish")

swish.unregister()
check("unregistering removes it", not hasattr(bpy.types, "SWISH_PT_main"))
check("... and leaves no app handlers behind", handler_counts() == handlers_before)

swish.register()
check("it registers again after unregistering", hasattr(bpy.types, "SWISH_PT_main"))

# Reload Scripts: unregister, reload the package in place, register again.
swish.unregister()
class_before = swish.panels.SWISH_PT_main
swish = importlib.reload(swish)
# A re-executed submodule defines its classes afresh.
check("reloading the package reloads its submodules", swish.panels.SWISH_PT_main is not class_before)
try:
    swish.register()
    check("it registers after a reload", hasattr(bpy.types, "SWISH_PT_main"))
except Exception as exc:
    check("it registers after a reload", False, exc)
swish.unregister()
check("... and unregisters after a reload, leaving no handlers", not hasattr(bpy.types, "SWISH_PT_main")
      and handler_counts() == handlers_before)

finish()
