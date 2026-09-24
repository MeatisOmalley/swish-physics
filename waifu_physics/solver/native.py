"""The C step, when this platform has it; numpy otherwise.

    backend = native.backend()      # the C step if its DLL loads, else step_numpy
    system.step_frame(dt, backend)

The DLL is bound to a System's arrays once (a C struct of pointers) and
rebound only when the System replaces an array, as set_shapes does. Both
steps take the same arrays and agree to the bit.
"""
import ctypes
import os
import sys

import numpy as np

from . import step_numpy

VERSION = 2
DLL_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin", "waifu_physics_step.dll")

_P = ctypes.c_void_p
# (field, dtype) in the order of WaifuPhysicsSystem in step.c.
POINTER_FIELDS = (
    ("parent", np.int32), ("group", np.int32), ("real_parent", np.int32), ("real_child", np.int32),
    ("kind", np.int8),
    ("alpha", np.float32), ("damping", np.float32), ("world_damping_location", np.float32),
    ("world_damping_rotation", np.float32), ("radius", np.float32), ("limit_angle", np.float32),
    ("pull", np.float32),
    ("loc", np.float64), ("prev", np.float64), ("pose", np.float64), ("pose_rot", np.float64),
    ("bridge_base", np.float64), ("push_sum", np.float64), ("push_weight", np.float32),
    ("gravity", np.float64), ("move", np.float64), ("move_rot", np.float64),
    ("teleport", np.int8), ("legacy_gravity", np.int8), ("planar_axis", np.int8), ("collision_only", np.int8),
    ("iterations_before", np.int32), ("iterations_after", np.int32), ("shape_start", np.int32),
    ("shape_count", np.int32), ("feedback_scale", np.float32),
    ("link_a", np.int32), ("link_b", np.int32), ("link_group", np.int32),
    ("link_length", np.float32), ("link_compliance", np.float32), ("lambda_", np.float32),
    ("shape_type", np.int8), ("shape_fallback", np.int8),
    ("shape_loc", np.float64), ("shape_rot", np.float64), ("shape_start_point", np.float64),
    ("shape_end_point", np.float64), ("shape_segment", np.float64), ("shape_segment_sq", np.float64),
    ("shape_fallback_dir", np.float64), ("shape_normal", np.float64), ("shape_plane_w", np.float64),
    ("shape_extent", np.float64), ("shape_fallback_center", np.float64),
    ("shape_radius0", np.float32), ("shape_radius1", np.float32), ("shape_fallback_radius", np.float32),
    ("wind_vel", np.float64), ("simple_force", np.float64), ("vforce", np.float64), ("pforce", np.float64),
    ("simple_on", np.int8), ("vmask", np.int8), ("pmask", np.int8),
)


class WaifuPhysicsSystem(ctypes.Structure):
    _fields_ = ([("n", ctypes.c_int), ("n_groups", ctypes.c_int), ("n_links", ctypes.c_int),
                 ("n_shapes", ctypes.c_int)]
                + [(name, _P) for name, _dtype in POINTER_FIELDS]
                + [("n_vforce", ctypes.c_int), ("n_pforce", ctypes.c_int)]
                + [("step_dt", ctypes.c_float), ("dt_old", ctypes.c_float)])


class CBackend:
    NAME = "c"

    def __init__(self, dll):
        self.dll = dll
        dll.waifu_physics_simulate_once.argtypes = [ctypes.POINTER(WaifuPhysicsSystem)]
        dll.waifu_physics_simulate_once.restype = None
        dll.waifu_physics_pull.argtypes = [ctypes.c_int, _P, ctypes.c_float, _P]
        dll.waifu_physics_pull.restype = None

    def _bind(self, s):
        """The struct for a System, rebuilt when any bound array was replaced."""
        arrays = [getattr(s, name) for name, _dtype in POINTER_FIELDS]
        key = tuple(id(a) for a in arrays)
        bound = getattr(s, "_c_bound", None)
        if bound is not None and bound[0] == key:
            return bound[1]
        struct = WaifuPhysicsSystem(n=s.n, n_groups=len(s.groups), n_links=len(s.link_a), n_shapes=len(s.shape_type))
        for (name, dtype), array in zip(POINTER_FIELDS, arrays):
            if array.dtype != dtype or not array.flags.c_contiguous:
                raise TypeError(f"{name}: expected contiguous {np.dtype(dtype)}, got {array.dtype}")
            setattr(struct, name, array.ctypes.data)
        s._c_bound = (key, struct, arrays)      # keeps the arrays alive with the struct
        return struct

    def simulate_once(self, s):
        struct = self._bind(s)
        struct.n_vforce = len(s.vforce)
        struct.n_pforce = len(s.pforce)
        struct.step_dt = float(s.step_dt)
        struct.dt_old = float(s.dt_old)
        self.dll.waifu_physics_simulate_once(ctypes.byref(struct))

    def pull(self, stiffness, exponent):
        """ApplyStiffnessPull's factor, 1 - (1 - Stiffness) ^ Exponent, with the C runtime's powf."""
        stiffness = np.ascontiguousarray(stiffness, dtype=np.float32)
        out = np.empty_like(stiffness)
        self.dll.waifu_physics_pull(len(stiffness), stiffness.ctypes.data, ctypes.c_float(exponent), out.ctypes.data)
        return out


_backend = None
_reason = ""


def backend():
    """The C step if its DLL is here and current; step_numpy otherwise."""
    global _backend, _reason
    if _backend is not None:
        return _backend
    if sys.platform != "win32":
        _reason = "no C step for this platform"
    elif not os.path.exists(DLL_PATH):
        _reason = "the C step has not been built"
    else:
        try:
            dll = ctypes.CDLL(DLL_PATH)
            dll.waifu_physics_version.restype = ctypes.c_int
            found = dll.waifu_physics_version()
            if found != VERSION:
                _reason = f"the C step is version {found}, expected {VERSION}"
            else:
                _backend = CBackend(dll)
                return _backend
        except OSError as exc:
            _reason = f"the C step would not load: {exc}"
    _backend = step_numpy
    return _backend


def reason():
    """Why the numpy step is in use, or an empty string."""
    backend()
    return _reason
