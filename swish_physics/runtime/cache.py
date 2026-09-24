"""The optional per-frame cache: snapshots to replay, and when to throw them away.

A snapshot holds everything the simulation needs to continue from a frame --
its points, the substep clock, the pose it interpolates from, the armatures'
previous transforms -- and the chain bones' channels as written, to replay.
Replaying a frame writes those channels back in frame_change_pre, before
Blender evaluates the frame, so a render sees them. Keyed chain channels
need a second write after their animation has been re-evaluated.

The cache is cleared on any change that affects the result: a group setting
or structure (property callbacks), keyframes (an Action update), a collider
or an armature edited by hand, a curve or collider node group edited, or the
scene's frame range or rate. The update Blender sends for our own writes is
recognised and ignored.
"""
import copy

import numpy as np

CHANNELS = (("location", 3), ("rotation_quaternion", 4), ("rotation_euler", 3), ("rotation_axis_angle", 4),
            ("scale", 3))


class Snapshot:
    def __init__(self, rt):
        s = rt.system
        self.loc, self.prev = s.loc.copy(), s.prev.copy()
        self.prev_pose, self.prev_pose_rot = s.prev_pose.copy(), s.prev_pose_rot.copy()
        self.scalars = (s.accumulator, s.dt_old, s.consume_fraction, s.pose_initialized, s.skip_known)
        self.forces = s.force_states()
        self.motions = [None if m.previous is None else tuple(v.copy() for v in m.previous) for m in rt.motions]
        self.channels = [{path: rig._buffers[path].copy() for path, _size in CHANNELS} for rig in rt.rigs]

    def restore_state(self, rt):
        s = rt.system
        s.loc[:], s.prev[:] = self.loc, self.prev
        s.prev_pose[:], s.prev_pose_rot[:] = self.prev_pose, self.prev_pose_rot
        s.accumulator, s.dt_old, s.consume_fraction, s.pose_initialized, s.skip_known = self.scalars
        s.set_force_states(self.forces)
        for motion, previous in zip(rt.motions, self.motions):
            motion.previous = None if previous is None else tuple(v.copy() for v in previous)

    def replay(self, rt):
        """Write the chain bones' channels as they were written for this frame."""
        rt.own_update = True
        for rig, channels in zip(rt.rigs, self.channels):
            bones = rig.obj.pose.bones
            rows = np.flatnonzero(rig.chain)
            for path, size in CHANNELS:
                buffer = rig._buffers[path]
                bones.foreach_get(path, buffer)
                values = buffer.reshape(-1, size)
                values[rows] = channels[path].reshape(-1, size)[rows]
                bones.foreach_set(path, buffer)
            rig.obj.update_tag(refresh={"DATA"})

    @classmethod
    def between(cls, earlier, later, fraction, rt):
        """Display pose between two canonical solver ticks, independent of scene FPS."""
        if fraction <= 0.0:
            return earlier
        if fraction >= 1.0:
            return later
        result = copy.copy(later)
        result.channels = []
        for rig, a, b in zip(rt.rigs, earlier.channels, later.channels):
            rows = np.flatnonzero(rig.chain)
            channels = {path: values.copy() for path, values in a.items()}
            for path, size in CHANNELS:
                start = a[path].reshape(-1, size)[rows]
                end = b[path].reshape(-1, size)[rows]
                output = channels[path].reshape(-1, size)
                if path == "rotation_quaternion":
                    output[rows] = _slerp(start, end, fraction)
                elif path == "rotation_axis_angle":
                    output[rows] = _axis_angle_from_quat(_slerp(
                        _quat_from_axis_angle(start), _quat_from_axis_angle(end), fraction))
                elif path == "rotation_euler":
                    delta = (end - start + np.pi) % (2 * np.pi) - np.pi
                    output[rows] = start + fraction * delta
                else:
                    output[rows] = start + fraction * (end - start)
            result.channels.append(channels)
        return result


def _slerp(a, b, fraction):
    dot = np.sum(a * b, axis=1)
    b = np.where((dot < 0.0)[:, None], -b, b)
    dot = np.clip(np.abs(dot), 0.0, 1.0)
    angle = np.arccos(dot)
    sine = np.sin(angle)
    blend = np.divide(np.sin(fraction * angle), sine,
                      out=np.full_like(angle, fraction), where=sine > 1e-6)
    first = np.divide(np.sin((1.0 - fraction) * angle), sine,
                      out=np.full_like(angle, 1.0 - fraction), where=sine > 1e-6)
    result = first[:, None] * a + blend[:, None] * b
    norm = np.linalg.norm(result, axis=1, keepdims=True)
    return result / np.where(norm > 0.0, norm, 1.0)


def _quat_from_axis_angle(values):
    half = values[:, 0] * 0.5
    return np.column_stack((np.cos(half), values[:, 1:] * np.sin(half)[:, None]))


def _axis_angle_from_quat(values):
    w = np.clip(values[:, 0], -1.0, 1.0)
    angle = 2.0 * np.arccos(w)
    sine = np.sqrt(np.maximum(0.0, 1.0 - w * w))
    axis = np.divide(values[:, 1:], sine[:, None],
                     out=np.tile((0.0, 1.0, 0.0), (len(values), 1)),
                     where=sine[:, None] > 1e-6)
    return np.column_stack((angle, axis))


def key(scene):
    """What a cache is only valid for: frame range and rates."""
    settings = scene.swish
    return (scene.frame_start, scene.frame_end, scene.render.fps, scene.render.fps_base, settings.target_framerate,
            settings.max_substeps, settings.fixed_substepping)


def collider_prints(rt):
    """What a user edit of a collider changes and its bone's motion does not: its transform
    relative to its bone, its bone, its shape and whether it is enabled."""
    from ..data import colliders
    armatures = [rig.obj for rig in rt.rigs]
    for g in range(len(rt.group_props)):
        _rig, props = rt._group(g)
        armatures += [item.armature for item in props.collider_sets if item.armature is not None]
    prints = {}
    for armature in armatures:
        for obj in armature.children:
            if colliders.is_collider(obj):
                local = obj.matrix_parent_inverse @ obj.matrix_basis
                prints[obj.name] = (tuple(round(v, 7) for row in local for v in row), obj.parent_bone,
                                    obj.swish_collider.enabled, repr(colliders.values(obj)))
    return prints


def action_print(rt, action):
    """What an action's curves say: keys and mute flags, but not the flags of curves Swish owns."""
    owned = {(c.data_path, c.array_index) for rig in rt.rigs if rig.keys is not None and rig.keys.muted
             for c, *_ in rig.keys.curves}
    parts = []
    for layer in action.layers:
        for strip in layer.strips:
            for bag in strip.channelbags:
                for curve in bag.fcurves:
                    points = curve.keyframe_points
                    co = np.empty(len(points) * 2, dtype=np.float32)
                    points.foreach_get("co", co)
                    muted = False if (curve.data_path, curve.array_index) in owned else curve.mute
                    parts.append(curve.data_path.encode() + bytes([curve.array_index, muted]) + co.tobytes())
    return hash(b"".join(parts))


def relevant_update(rt, depsgraph):
    """Does this depsgraph update change the simulation's result? Our own writes do not.

    Colliders are judged by their prints, since our writes move bone-parented
    colliders too; armatures by whether the update is our own."""
    import bpy
    from ..data import colliders, curves
    rig_objects = {rig.obj.name for rig in rt.rigs}
    rig_data = {rig.obj.data.name for rig in rt.rigs}
    own = rt.own_update
    rt.own_update = False
    colliders_touched = False
    for update in depsgraph.updates:
        found = update.id
        if isinstance(found, bpy.types.Action):
            # Swish mutes and unmutes the chains' curves itself (while simulating, around saves);
            # only a change to what the keys say counts.
            printed = action_print(rt, found)
            if rt.action_prints.get(found.name) != printed:
                rt.action_prints[found.name] = printed
                return True
            continue
        if isinstance(found, bpy.types.NodeTree) and found.name in (curves.HOST, colliders.TREE):
            return True
        if isinstance(found, bpy.types.Armature) and found.name in rig_data and not own:
            return True
        if isinstance(found, bpy.types.Object):
            if found.name in rig_objects and not own:
                return True
            if found.field is not None and found.field.type == "WIND":       # the scene's wind
                return True
            if colliders.is_collider(found):
                colliders_touched = True
    if colliders_touched and collider_prints(rt) != rt.collider_prints:
        return True
    return False
