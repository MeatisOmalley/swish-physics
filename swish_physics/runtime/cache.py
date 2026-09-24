"""The optional per-frame cache: snapshots to replay, and when to throw them away.

A snapshot holds everything the simulation needs to continue from a frame --
its points, the substep clock, the pose it interpolates from, the armatures'
previous transforms -- and the chain bones' channels as written, to replay.
Replaying a frame writes those channels back in frame_change_pre, before
Blender evaluates the frame, so a render sees them; frame_change_post writes
them again, after keyed chain channels were re-evaluated.

The cache is cleared on any change that affects the result: a group setting
or structure (property callbacks), keyframes (an Action update), a collider
or an armature edited by hand, a curve or collider node group edited, or the
scene's frame range or rate. The update Blender sends for our own writes is
recognised and ignored.
"""
import numpy as np

CHANNELS = (("location", 3), ("rotation_quaternion", 4), ("rotation_euler", 3), ("rotation_axis_angle", 4))


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


def key(scene):
    """What a cache is only valid for: frame range and rates."""
    settings = scene.swish
    return (scene.frame_start, scene.render.fps, scene.render.fps_base, settings.target_framerate,
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
            return True
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
