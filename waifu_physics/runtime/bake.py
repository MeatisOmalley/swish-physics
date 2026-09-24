"""Bake: the cached simulation written as keyframes, so it plays, renders and exports without the add-on.

Each armature's action is copied to "<action> Baked" and the copy assigned; the original keeps a fake user,
so switching back is one click in the Action editor. In the copy, the chain bones' channels are replaced by
one linear key per cached frame: rotation always (in the bone's own rotation mode), location and scale only
where the simulation moved them. The keys are written in bulk (foreach_set), not one insert at a time."""
import bpy
import numpy as np
from bpy_extras import anim_utils

from . import cache as frame_cache
from . import io

_LINEAR = 1                          # the Keyframe.interpolation enum's index of 'LINEAR'
_STILL = 1.0e-6                      # a channel that moves less than this over the bake is left as it was


def _rotation(mode):
    if mode == io.QUATERNION:
        return "rotation_quaternion"
    if mode == io.AXIS_ANGLE:
        return "rotation_axis_angle"
    return "rotation_euler"


def collect(rt):
    """What to bake, read from the cache before the runtime lets the chains go: the cached frames, and per
    armature its object, chain bones, their rotation modes and every channel's values (frames, bones, size)."""
    frames = sorted(rt.cache)
    found = []
    for r, rig in enumerate(rt.rigs):
        rows = np.flatnonzero(rig.chain)
        if not len(rows) or not rig.alive():
            continue
        channels = {path: np.stack([rt.cache[f].channels[r][path].reshape(-1, size)[rows] for f in frames])
                    for path, size in frame_cache.CHANNELS}
        found.append((rig.obj, [rig.names[i] for i in rows], [int(rig._modes[i]) for i in rows], channels))
    return frames, found


def _continuous(path, values):
    """Keys interpolate linearly between frames: quaternions kept in one hemisphere and Euler angles unwrapped,
    so no frame swings the long way round."""
    values = values.astype(np.float64)
    if path == "rotation_quaternion":
        for i in range(1, len(values)):
            if np.dot(values[i], values[i - 1]) < 0.0:
                values[i] = -values[i]
    elif path == "rotation_euler":
        values = np.unwrap(values, axis=0)
    return values


def _baked_action(obj):
    """Copy the armature's action (or start one) as "<name> Baked", assign it, and return its channelbag."""
    data = obj.animation_data or obj.animation_data_create()
    original, slot = data.action, data.action_slot
    if original is not None:
        original.use_fake_user = True                    # kept, to switch back to
        action = original.copy()
        action.name = f"{original.name} Baked"
        data.action = action
        found = next((s for s in action.slots if slot is not None and s.identifier == slot.identifier), None)
        data.action_slot = found if found is not None else action.slots.new(id_type="OBJECT", name=obj.name)
    else:
        action = bpy.data.actions.new(f"{obj.name} Baked")
        data.action = action
        data.action_slot = action.slots.new(id_type="OBJECT", name=obj.name)
    return action, anim_utils.action_ensure_channelbag_for_slot(action, data.action_slot)


def _write(bag, data_path, bone, frames, values):
    for axis in range(values.shape[1]):
        curve = bag.fcurves.find(data_path, index=axis) or bag.fcurves.new(data_path, index=axis, group_name=bone)
        curve.mute = False
        points = curve.keyframe_points
        points.clear()
        points.add(len(frames))
        points.foreach_set("co", np.column_stack((frames, values[:, axis])).ravel())
        points.foreach_set("interpolation", np.full(len(frames), _LINEAR, dtype=np.int32))
        curve.update()


def write(frames, found):
    """Write collect()'s result. Returns the baked actions."""
    frames = np.asarray(frames, dtype=np.float64)
    actions = []
    for obj, bones, modes, channels in found:
        action, bag = _baked_action(obj)
        for b, (bone, mode) in enumerate(zip(bones, modes)):
            base = f'pose.bones["{bpy.utils.escape_identifier(bone)}"].'
            for path in ("location", _rotation(mode), "scale"):
                values = channels[path][:, b, :]
                if path in ("location", "scale") and np.ptp(values, axis=0).max() < _STILL:
                    continue
                _write(bag, base + path, bone, frames, _continuous(path, values))
        actions.append(action)
    return actions
