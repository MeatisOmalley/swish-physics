"""Swish Physics: bone-chain physics for Blender, ported from Kawaii Physics."""

# Reload Scripts re-runs this file with its old globals still in place; reload
# the submodules too (dependencies first), so an edit takes effect without a restart.
if "bpy" in locals():
    import importlib
    for _module in _RELOAD_ORDER:
        importlib.reload(_module)
else:
    from .solver import uemath, system, curves as solver_curves, step_numpy, native, build
    from .data import props, curves
    from .runtime import io, live
    from .ui import selection, ops, panels

import bpy  # noqa: E402,F401  (its presence marks a reload, above)

_RELOAD_ORDER = (uemath, system, solver_curves, step_numpy, native, build, props, curves, io, live, selection,
                 ops, panels)

# Registered in this order, unregistered in reverse.
MODULES = (props, live, selection, ops, panels)


def register():
    for module in MODULES:
        module.register()


def unregister():
    for module in reversed(MODULES):
        module.unregister()
