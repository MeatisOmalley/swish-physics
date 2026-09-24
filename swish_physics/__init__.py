"""Swish Physics: bone-chain physics for Blender, ported from Kawaii Physics."""

# Reload Scripts re-runs this file with its old globals still in place; reload
# the submodules too, so an edit to any of them takes effect without a restart.
if "bpy" in locals():
    import importlib
    panels = importlib.reload(panels)
else:
    from .ui import panels

import bpy  # noqa: E402,F401  (its presence marks a reload, above)

# Registered in this order, unregistered in reverse.
MODULES = (panels,)


def register():
    for module in MODULES:
        module.register()


def unregister():
    for module in reversed(MODULES):
        module.unregister()
