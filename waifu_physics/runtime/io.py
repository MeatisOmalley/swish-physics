"""Armatures in and out of the solver: pose input, rotations back, in bulk.

The solver works in Kawaii's units, centimetres, in the armature's own space
(Kawaii's component space). Input is the animated pose: every bone as Blender
evaluated it, except the simulated chain bones, whose pose is rebuilt from
their keyed channels (or their rest, where a channel is not keyed) -- their
evaluated matrices still hold the previous frame's physics. Output is each
chain bone's local rotation, written in the bone's own rotation mode.
"""
import numpy as np
from mathutils import Euler, Matrix, Quaternion

from . import keys as chain_keys

EULER_ORDERS = {1: "XYZ", 2: "XZY", 3: "YXZ", 4: "YZX", 5: "ZXY", 6: "ZYX"}   # rotation_mode as foreach_get reads it
QUATERNION, AXIS_ANGLE = 0, -1


def cm_per_unit(scene):
    """Centimetres in one Blender unit."""
    return 100.0 * scene.unit_settings.scale_length


def animated_channels(obj):
    """{bone name: {"location", "rotation", "scale"}} that animation drives: the action's
    slot, unmuted NLA strips and drivers."""
    found = {}
    data = obj.animation_data
    if data is None:
        return found

    def note(path):
        if not path.startswith('pose.bones["'):
            return
        end = path.find('"]', 12)
        if end < 0:
            return
        name, prop = path[12:end], path[end + 3:]
        kind = "rotation" if prop.startswith("rotation_") else prop
        if kind in ("location", "rotation", "scale"):
            found.setdefault(name, set()).add(kind)

    def scan(action, slot):
        if action is None or slot is None:
            return
        for layer in getattr(action, "layers", ()):
            for strip in layer.strips:
                bag = strip.channelbag(slot)
                if bag is not None:
                    for curve in bag.fcurves:
                        note(curve.data_path)

    scan(data.action, data.action_slot)
    for track in data.nla_tracks:
        if not track.mute:
            for strip in track.strips:
                if not strip.mute:
                    scan(strip.action, getattr(strip, "action_slot", None))
    for driver in data.drivers:
        note(driver.data_path)
    return found


def unscaled(m):
    """The rotation of matrices that may carry scale: columns normalised."""
    return m / np.linalg.norm(m, axis=1, keepdims=True)


def quats_from_matrices(m):
    """Rotation matrices to quaternions in Unreal's (x, y, z, w) order (Shepperd's method)."""
    n = len(m)
    q = np.empty((n, 4))
    trace = m[:, 0, 0] + m[:, 1, 1] + m[:, 2, 2]
    for i in range(n):
        r = m[i]
        if trace[i] > 0.0:
            s = np.sqrt(trace[i] + 1.0) * 2.0
            q[i] = ((r[2, 1] - r[1, 2]) / s, (r[0, 2] - r[2, 0]) / s, (r[1, 0] - r[0, 1]) / s, 0.25 * s)
        elif r[0, 0] > r[1, 1] and r[0, 0] > r[2, 2]:
            s = np.sqrt(1.0 + r[0, 0] - r[1, 1] - r[2, 2]) * 2.0
            q[i] = (0.25 * s, (r[0, 1] + r[1, 0]) / s, (r[0, 2] + r[2, 0]) / s, (r[2, 1] - r[1, 2]) / s)
        elif r[1, 1] > r[2, 2]:
            s = np.sqrt(1.0 + r[1, 1] - r[0, 0] - r[2, 2]) * 2.0
            q[i] = ((r[0, 1] + r[1, 0]) / s, 0.25 * s, (r[1, 2] + r[2, 1]) / s, (r[0, 2] - r[2, 0]) / s)
        else:
            s = np.sqrt(1.0 + r[2, 2] - r[0, 0] - r[1, 1]) * 2.0
            q[i] = ((r[0, 2] + r[2, 0]) / s, (r[1, 2] + r[2, 1]) / s, 0.25 * s, (r[1, 0] - r[0, 1]) / s)
    return q / np.linalg.norm(q, axis=1, keepdims=True)


def matrices_from_quats(q):
    """(x, y, z, w) quaternions to rotation matrices."""
    x, y, z, w = q[:, 0], q[:, 1], q[:, 2], q[:, 3]
    return np.stack([
        np.stack([1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)], axis=1),
        np.stack([2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)], axis=1),
        np.stack([2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)], axis=1)], axis=1)


class Rig:
    """One armature's bones, read and written in bulk."""

    def __init__(self, obj):
        self.obj = obj
        bones = obj.pose.bones
        self.count = len(bones)
        self.names = [pb.name for pb in bones]
        self.index = {name: i for i, name in enumerate(self.names)}
        self.parents = np.array([self.index[pb.parent.name] if pb.parent else -1 for pb in bones])
        rest = np.array([np.array(pb.bone.matrix_local) for pb in bones]).reshape(self.count, 4, 4)
        self.rest = rest
        parent_rest = np.where((self.parents >= 0)[:, None, None], rest[np.maximum(self.parents, 0)], np.eye(4))
        self.rest_rel = np.linalg.inv(parent_rest) @ rest              # rest offset from the parent
        self._matrices = np.empty(self.count * 16, dtype=np.float32)
        self._modes = np.empty(self.count, dtype=np.int32)
        self._buffers = {path: np.empty(self.count * size, dtype=np.float32) for path, size in
                         (("location", 3), ("rotation_quaternion", 4), ("rotation_euler", 3),
                          ("rotation_axis_angle", 4), ("scale", 3))}
        self.chain = np.zeros(self.count, dtype=bool)
        self.chain_levels = []
        self.keyed = {}
        self.keys = None
        self.constrained = np.zeros(self.count, dtype=bool)

    # ------------------------------------------------------------------ setup
    def set_chain(self, bone_indices):
        """The bones the solver moves; their pose is rebuilt from keyed channels, parents first."""
        self.chain[:] = False
        self.chain[list(bone_indices)] = True
        depth = np.zeros(self.count, dtype=int)
        for i in range(self.count):
            p = self.parents[i]
            depth[i] = depth[p] + 1 if p >= 0 else 0
        rows = np.flatnonzero(self.chain)
        self.chain_levels = [rows[depth[rows] == d] for d in sorted(set(depth[rows]))]
        self.refresh_keyed()
        if self.keys is not None:
            self.keys.unmute()
        self.keys = chain_keys.ChainKeys(self)
        bones = self.obj.pose.bones
        self.constrained = np.array([any(c.enabled and c.influence > 0 for c in pb.constraints) for pb in bones])

    def subtree(self, roots, excluded):
        """Bones under the roots, as Kawaii collects them: an excluded bone cuts off its subtree."""
        excluded = set(excluded)
        found, stack = [], [self.index[r] for r in roots if r in self.index and r not in excluded]
        children = {}
        for i, p in enumerate(self.parents):
            children.setdefault(int(p), []).append(i)
        while stack:
            i = stack.pop()
            found.append(i)
            stack += [c for c in children.get(i, []) if self.names[c] not in excluded]
        return sorted(set(found))

    def refresh_keyed(self):
        channels = animated_channels(self.obj)
        self.keyed = {kind: np.array([kind in channels.get(name, ()) for name in self.names])
                      for kind in ("location", "rotation", "scale")}

    def ref_lengths(self):
        """Each bone's rest offset from its parent (Kawaii's BoneLength), in Blender units."""
        return np.linalg.norm(self.rest_rel[:, :3, 3], axis=1)

    # ------------------------------------------------------------------ input
    def read(self):
        """The input pose, Blender's evaluated matrices in armature space, and the chain bones'
        local basis matrices from their keyed channels.

        The evaluated pose is the input only because restore() cleared last frame's physics
        from the chain channels before Blender evaluated it (frame_change_pre), so constraints,
        drivers and IK on any bone reach the solver."""
        bones = self.obj.pose.bones
        bones.foreach_get("matrix", self._matrices)
        evaluated = self._matrices.reshape(self.count, 4, 4).transpose(0, 2, 1).astype(np.float64)
        bones.foreach_get("rotation_mode", self._modes)
        for path, buffer in self._buffers.items():
            bones.foreach_get(path, buffer)
        self.basis = self._basis()
        self.pose = evaluated
        return evaluated

    def read_ahead(self):
        """The input before Blender evaluates the frame (live's one-evaluation path): bones
        outside the chains, and constrained chain bones, as Blender last evaluated them -- a
        frame late -- and the chain rebuilt from its channels, which restore(frame) has just set
        to this frame's keys or rest."""
        bones = self.obj.pose.bones
        bones.foreach_get("matrix", self._matrices)
        evaluated = self._matrices.reshape(self.count, 4, 4).transpose(0, 2, 1).astype(np.float64)
        bones.foreach_get("rotation_mode", self._modes)
        for path, buffer in self._buffers.items():
            bones.foreach_get(path, buffer)
        basis = self._basis()
        pose = evaluated.copy()
        for level in self.chain_levels:
            parent = self.parents[level]
            parent_pose = np.where((parent >= 0)[:, None, None], pose[np.maximum(parent, 0)], np.eye(4))
            rebuilt = parent_pose @ self.rest_rel[level] @ basis[level]
            pose[level] = np.where(self.constrained[level][:, None, None], evaluated[level], rebuilt)
        self.basis = basis
        self.pose = pose
        return pose

    def _basis(self):
        """The chain bones' local basis matrices from their keyed channels (rest where unkeyed)."""
        basis = np.tile(np.eye(4), (self.count, 1, 1))
        rows = np.flatnonzero(self.chain)
        keyed_loc = rows[self.keyed["location"][rows]]
        keyed_rot = rows[self.keyed["rotation"][rows]]
        keyed_scale = rows[self.keyed["scale"][rows]]
        location = self._buffers["location"].reshape(-1, 3)
        scale = self._buffers["scale"].reshape(-1, 3)
        for i in keyed_rot:
            basis[i, :3, :3] = np.array(self._rotation_of(i).to_matrix())
        for i in keyed_scale:
            basis[i, :3, :3] = basis[i, :3, :3] * scale[i]
        basis[keyed_loc, :3, 3] = location[keyed_loc]
        return basis

    def _rotation_of(self, i):
        mode = int(self._modes[i])
        if mode == QUATERNION:
            return Quaternion(self._buffers["rotation_quaternion"][4 * i:4 * i + 4])
        if mode == AXIS_ANGLE:
            angle, x, y, z = self._buffers["rotation_axis_angle"][4 * i:4 * i + 4]
            return Quaternion((x, y, z), angle)
        return Euler(self._buffers["rotation_euler"][3 * i:3 * i + 3], EULER_ORDERS[mode]).to_quaternion()

    # ----------------------------------------------------------------- output
    def write(self, bones, rotation, location=None, move_location=None):
        """Give chain bones their armature-space rotations (x y z w).

        bones: the rig's bone indices; rotation: their rotations; location and
        move_location: armature-space head positions for the bones Kawaii
        places directly (every bone below a group's root), and which those are. Everything is converted to local basis channels, parents
        first, and written in one call per channel array."""
        target_rot = np.zeros((self.count, 3, 3))
        target_rot[bones] = matrices_from_quats(rotation)
        placed = np.zeros(self.count, dtype=bool)
        head = np.zeros((self.count, 3))
        if location is not None and move_location is not None:
            placed[bones[move_location]] = True
            head[bones[move_location]] = location[move_location]
        out = self.pose.copy()
        local_rot = {}
        local_loc = {}
        for level in self.chain_levels:
            parent = self.parents[level]
            parent_out = np.where((parent >= 0)[:, None, None], out[np.maximum(parent, 0)], np.eye(4))
            frame = parent_out @ self.rest_rel[level]                   # where the bone's basis starts
            frame_rot = unscaled(frame[:, :3, :3])
            local = np.einsum("nji,njk->nik", frame_rot, target_rot[level])
            basis = self.basis[level].copy()
            scale = np.linalg.norm(basis[:, :3, :3], axis=1)
            basis[:, :3, :3] = local * scale[:, None, :]
            moved = placed[level]
            if moved.any():
                inverse = np.linalg.inv(frame[moved])
                point = np.concatenate([head[level[moved]], np.ones((moved.sum(), 1))], axis=1)
                basis[moved, :3, 3] = np.einsum("nij,nj->ni", inverse, point)[:, :3]
            out[level] = frame @ basis
            for k, i in enumerate(level):
                local_rot[i] = local[k]
                if moved[k]:
                    local_loc[i] = basis[k, :3, 3]
        self._write_channels(local_rot, local_loc)
        return out

    def _write_channels(self, local_rot, local_loc):
        """One foreach_set per changed channel array, each bone in its own rotation mode."""
        bones = self.obj.pose.bones
        changed = set()
        quat = self._buffers["rotation_quaternion"].reshape(-1, 4)
        euler = self._buffers["rotation_euler"].reshape(-1, 3)
        axis_angle = self._buffers["rotation_axis_angle"].reshape(-1, 4)
        for i, rot in local_rot.items():
            q = Matrix(rot.tolist()).to_quaternion()
            mode = int(self._modes[i])
            if mode == QUATERNION:
                quat[i] = q
                changed.add("rotation_quaternion")
            elif mode == AXIS_ANGLE:
                axis, angle = q.to_axis_angle()
                axis_angle[i] = (angle, axis.x, axis.y, axis.z)
                changed.add("rotation_axis_angle")
            elif mode in EULER_ORDERS:
                # Compatible with the current value, so angles do not flip between frames.
                euler[i] = q.to_euler(EULER_ORDERS[mode], Euler(euler[i].tolist(), EULER_ORDERS[mode]))
                changed.add("rotation_euler")
        if local_loc:
            location = self._buffers["location"].reshape(-1, 3)
            for i, loc in local_loc.items():
                location[i] = loc
            changed.add("location")
        for path in changed:
            bones.foreach_set(path, self._buffers[path])
        if changed:
            self.obj.update_tag(refresh={"DATA"})

    def restore(self, frame=None):
        """Put the chain bones back to their input channels: keyed values, or rest. Before a frame
        is evaluated this clears last frame's physics, so the evaluated pose is a clean input.
        Keys Waifu Physics has taken over (muted) are sampled at frame; with no frame they are left."""
        bones = self.obj.pose.bones
        rows = np.flatnonzero(self.chain)
        paths = (("location", 3, 0.0), ("rotation_quaternion", 4, None), ("rotation_euler", 3, 0.0),
                 ("rotation_axis_angle", 4, None), ("scale", 3, 1.0))
        for path, size, rest_value in paths:
            kind = "rotation" if path.startswith("rotation") else path
            buffer = self._buffers[path]
            bones.foreach_get(path, buffer)
            values = buffer.reshape(-1, size)
            reset = rows[~self.keyed[kind][rows]]
            if path == "rotation_quaternion":
                values[reset] = (1.0, 0.0, 0.0, 0.0)
            elif path == "rotation_axis_angle":
                values[reset] = (0.0, 0.0, 1.0, 0.0)
            else:
                values[reset] = rest_value
        if frame is not None and self.keys is not None and self.keys.muted:
            self.keys.sample(frame, self._buffers)
        for path, _size, _rest in paths:
            bones.foreach_set(path, self._buffers[path])
        self.obj.update_tag(refresh={"DATA"})
