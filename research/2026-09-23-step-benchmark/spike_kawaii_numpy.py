"""Spike: Kawaii Physics' step in numpy on Peach's dress and hair rigs -- how long does a frame take?

Follows AnimNode_KawaiiPhysicsSimulation.cpp / ...Collision.cpp (master, 2026-09-20):
per substep -- roots follow the pose; every other point: Verlet velocity, damping,
gravity, integrate, stiffness pull toward the pose (parent first); XPBD links;
sphere/capsule collisions; XPBD links again; angle limit and length restore
(parent first); then rotations from positions, written back in bulk.

Parent-first work runs one chain depth at a time, which gives the same result as
Kawaii's bone-by-bone order. Links run in batches that share no point; Kawaii
solves them one after another, so that part converges slightly differently.
Timing only: world-move follow, planar constraints and wind are left out.
"""
import math
import sys
import time

sys.path.insert(0, "C:/Users/meat/Documents/blender/code/vroid edits/vroid transfer")
import bpy
import numpy as np
from mathutils import Matrix

bpy.ops.preferences.addon_enable(module="bl_ext.blender_org.vrm")
import waifusim_vroid_swap as addon
addon.register()
preview = sys.modules["waifusim_vroid_swap.preview"]
springsim = sys.modules["waifusim_vroid_swap.springsim"]
P = r"C:\Users\meat\Documents\blender\assets\VROID ASSETS\assets\characters\Princess Peach Pack VRoid Files"
bpy.ops.object.select_all(action="SELECT"); bpy.ops.object.delete()
s = bpy.context.scene.ws_alpha
s.vroid = P + r"\Peach MAXED\Princess Peach maxed.vroid"
s.normal_vrm = P + r"\Peach Final.vrm"
s.zeroed_vrm = P + r"\Princess Peach Pack VRoid Files - sliders zeroed\peach_zeroed.vrm"
bpy.ops.ws_alpha.scan(); bpy.ops.ws_alpha.instantiate()

FPS, STEP = 24, 1.0 / 60.0
COMPLIANCE_LEATHER = 0.000000001


def batch_rotation_between(u, v):
    """Rotation matrices taking each u onto each v (FQuat::FindBetweenVectors, as matrices)."""
    u = u / np.linalg.norm(u, axis=1, keepdims=True)
    v = v / np.linalg.norm(v, axis=1, keepdims=True)
    axis = np.cross(u, v)
    sin = np.linalg.norm(axis, axis=1)
    cos = (u * v).sum(1)
    k = np.divide(axis, sin[:, None], out=np.zeros_like(axis), where=sin[:, None] > 1e-12)
    kx, ky, kz = k[:, 0], k[:, 1], k[:, 2]
    zero = np.zeros_like(kx)
    K = np.stack([np.stack([zero, -kz, ky], 1), np.stack([kz, zero, -kx], 1), np.stack([-ky, kx, zero], 1)], 1)
    eye = np.broadcast_to(np.eye(3), K.shape)
    return eye + sin[:, None, None] * K + (1 - cos)[:, None, None] * (K @ K)


def batch_quaternions(m):
    """Rotation matrices to (w, x, y, z), vectorised Shepperd."""
    w = np.sqrt(np.maximum(0.0, 1 + m[:, 0, 0] + m[:, 1, 1] + m[:, 2, 2])) / 2
    x = np.sqrt(np.maximum(0.0, 1 + m[:, 0, 0] - m[:, 1, 1] - m[:, 2, 2])) / 2
    y = np.sqrt(np.maximum(0.0, 1 - m[:, 0, 0] + m[:, 1, 1] - m[:, 2, 2])) / 2
    z = np.sqrt(np.maximum(0.0, 1 - m[:, 0, 0] - m[:, 1, 1] + m[:, 2, 2])) / 2
    x = np.copysign(x, m[:, 2, 1] - m[:, 1, 2])
    y = np.copysign(y, m[:, 0, 2] - m[:, 2, 0])
    z = np.copysign(z, m[:, 1, 0] - m[:, 0, 1])
    return np.stack([w, x, y, z], 1)


class Sim:
    def __init__(self, rig):
        self.rig = rig
        bones = rig.pose.bones
        self.count = len(bones)
        index = {pb.name: i for i, pb in enumerate(bones)}
        chain = [pb for pb in bones if pb.name.startswith("J_Sec")]
        roots = [pb for pb in chain if not (pb.parent and pb.parent.name.startswith("J_Sec"))]
        # Nodes: chain roots (kinematic), every other chain bone, then a dummy past each tip.
        nodes, parent, depth, bone_of, anchor = [], [], [], [], []
        order = {}

        def add(pb, par, d, root):
            order[pb.name] = len(nodes)
            nodes.append(pb.name); parent.append(par); depth.append(d); bone_of.append(index[pb.name])
            anchor.append(root)
            for child in pb.children:
                if child.name.startswith("J_Sec"):
                    add(child, order[pb.name], d + 1, root)

        for root in roots:
            add(root, -1, 0, len(nodes))
        real = len(nodes)
        rest = np.array([np.array(bones[n].bone.matrix_local) for n in nodes])
        tips = [i for i in range(real) if not any(p == i for p in parent)]
        for tip in tips:
            nodes.append(nodes[tip] + " dummy"); parent.append(tip); depth.append(depth[tip] + 1)
            bone_of.append(-1); anchor.append(anchor[tip])
        self.n = len(nodes)
        self.real = real
        self.parent = np.array(parent)
        self.depth = np.array(depth)
        self.bone_of = np.array(bone_of)
        self.anchor = np.array(anchor)
        # Rest head of every node in armature space; a dummy sits one bone length past its tip.
        heads = np.zeros((self.n, 3))
        heads[:real] = rest[:, :3, 3]
        for k, tip in enumerate(tips):
            pb = bones[nodes[tip]]
            heads[real + k] = np.array(pb.bone.tail_local)
        self.rest_heads = heads
        self.levels = [np.flatnonzero(self.depth == d) for d in range(1, self.depth.max() + 1)]
        self.roots = np.flatnonzero(self.parent < 0)
        # Each node rides its chain root's pose rigidly in rest shape: pose = anchor pose @ offset.
        self.root_parent_bone = np.array([index[bones[nodes[r]].parent.name] for r in self.roots])
        self.root_parent_rest_inv = np.array([np.linalg.inv(np.array(bones[nodes[r]].parent.bone.matrix_local))
                                              for r in self.roots])
        self.root_rest = rest[self.roots]
        root_slot = {r: k for k, r in enumerate(self.roots)}
        self.root_slot = np.array([root_slot[a] for a in self.anchor])
        local = np.array([np.linalg.inv(rest[self.anchor[i]]) @ np.append(heads[i], 1.0) for i in range(self.n)])
        self.local_heads = local[:, :3]
        # Rotation of each bone relative to its chain root at rest, and to its parent at rest.
        self.rel_root = np.array([(np.linalg.inv(rest[self.anchor[i]]) @ rest[i])[:3, :3] for i in range(real)])
        self.rel_parent = np.array([
            (np.linalg.inv(rest[parent[i]]) @ rest[i])[:3, :3] if parent[i] >= 0 else
            (np.linalg.inv(np.array(bones[nodes[i]].parent.bone.matrix_local)) @ rest[i])[:3, :3]
            for i in range(real)])
        children = [np.flatnonzero(self.parent == i) for i in range(real)]
        self.aimed = np.array([i for i in range(real) if len(children[i]) == 1])
        self.aim_child = np.array([children[i][0] for i in self.aimed])
        # Settings (Kawaii defaults-ish), per point.
        self.damping = np.full(self.n, 0.1)
        self.stiffness = np.full(self.n, 0.05)
        self.radius = np.full(self.n, 0.02)
        self.limit = np.full(self.n, math.radians(60.0))
        self.gravity = np.array([0.0, 0.0, -9.81])
        # Colliders from the rig's .vrm data: (bone index, offset, radius, tail or None).
        springs = preview._spring_bones(rig)
        prepared = springsim._prepare(rig, springs)
        self.spheres = [(b, np.array(o), r) for b, o, r, t in prepared.colliders.values() if t is None]
        self.capsules = [(b, np.array(o), r, np.array(t)) for b, o, r, t in prepared.colliders.values() if t is not None]
        # Links: skirt chains in a ring around the hips, joined point by point at each depth.
        self.links = self._skirt_links(nodes)
        self.link_lambda = np.zeros(len(self.links))
        self.link_batches = self._colour(self.links)
        self.link_rest = (np.linalg.norm(heads[self.links[:, 0]] - heads[self.links[:, 1]], axis=1)
                          if len(self.links) else np.zeros(0))
        self.phases = {}
        self.matrices = np.empty(self.count * 16, dtype=np.float32)
        self.quats = np.empty(self.count * 4, dtype=np.float32)
        self.loc = self.prev = None
        self.pose_prev = None
        print(f"SPIKE| {rig['ws_alpha_item']}: {self.n} points ({real} bones, {len(tips)} tip dummies), "
              f"{len(self.roots)} chains, depth {self.depth.max()}, {len(self.spheres)} spheres, "
              f"{len(self.capsules)} capsules, {len(self.links)} links in {len(self.link_batches)} batches")

    def _skirt_links(self, nodes):
        skirt_roots = [r for r in self.roots if "Skirt" in nodes[r]]
        if len(skirt_roots) < 2:
            return np.zeros((0, 2), dtype=int)
        centre = self.rest_heads[skirt_roots].mean(0)
        angles = [math.atan2(*(self.rest_heads[r] - centre)[[1, 0]]) for r in skirt_roots]
        ring = [r for _a, r in sorted(zip(angles, skirt_roots))]
        columns = []
        for r in ring:
            column, node = [r], r
            while True:
                children = np.flatnonzero(self.parent == node)
                if not len(children):
                    break
                node = children[0]
                column.append(node)
            columns.append(column)
        links = []
        for a, b in zip(columns, columns[1:] + columns[:1]):
            for depth in range(1, min(len(a), len(b))):
                links.append((a[depth], b[depth]))
        return np.array(links)

    @staticmethod
    def _colour(links):
        """Batches of links that share no point, so each batch solves at once."""
        batches, left = [], list(range(len(links)))
        while left:
            used, batch, rest = set(), [], []
            for k in left:
                a, b = links[k]
                if a in used or b in used:
                    rest.append(k)
                else:
                    batch.append(k); used.update((a, b))
            batches.append(np.array(batch)); left = rest
        return batches

    def read_pose(self):
        """Every node's pose position this frame, riding its chain root's parent."""
        self.rig.pose.bones.foreach_get("matrix", self.matrices)
        mats = self.matrices.reshape(self.count, 4, 4).transpose(0, 2, 1).astype(np.float64)
        root_pose = mats[self.root_parent_bone] @ self.root_parent_rest_inv @ self.root_rest
        self.root_pose = root_pose
        heads = np.einsum("nij,nj->ni", root_pose[self.root_slot][:, :3, :3], self.local_heads) \
            + root_pose[self.root_slot][:, :3, 3]
        self.collider_bones = mats
        return heads

    def colliders(self):
        mats = self.collider_bones
        spheres = [(mats[b][:3, :3] @ o + mats[b][:3, 3], r) for b, o, r in self.spheres]
        capsules = [(mats[b][:3, :3] @ o + mats[b][:3, 3], mats[b][:3, :3] @ t + mats[b][:3, 3], r)
                    for b, o, r, t in self.capsules]
        return spheres, capsules

    def links_pass(self):
        if not len(self.links):
            return
        compliance = COMPLIANCE_LEATHER / (STEP * STEP)
        for batch in self.link_batches:
            a, b = self.links[batch, 0], self.links[batch, 1]
            delta = self.loc[b] - self.loc[a]
            length = np.linalg.norm(delta, axis=1)
            ok = length > 0
            dl = (length - self.link_rest[batch] - compliance * self.link_lambda[batch]) / (2 + compliance)
            move = np.where(ok[:, None], delta / np.where(ok, length, 1)[:, None] * dl[:, None], 0)
            self.loc[a] += move
            self.loc[b] -= move
            self.link_lambda[batch] += np.where(ok, dl, 0)

    def substep(self, pose, dt_old):
        clock = time.perf_counter
        mark = clock()
        loc, prev = self.loc, self.prev
        moving = self.parent >= 0
        prev[self.roots] = loc[self.roots]
        loc[self.roots] = pose[self.roots]
        idx = np.flatnonzero(moving)
        velocity = (loc[idx] - prev[idx]) / dt_old
        prev[idx] = loc[idx]
        velocity *= (1 - self.damping[idx])[:, None]
        velocity += self.gravity * STEP
        loc[idx] += velocity * STEP
        mark = self._lap("integrate", mark)
        pull = 1 - (1 - self.stiffness) ** (60 * STEP)
        for level in self.levels:
            par = self.parent[level]
            base = loc[par] + (pose[level] - pose[par])
            loc[level] += (base - loc[level]) * pull[level][:, None]
        mark = self._lap("stiffness pull", mark)
        self.link_lambda[:] = 0
        self.links_pass()
        mark = self._lap("links", mark)
        spheres, capsules = self.colliders()
        pts, rad = loc[idx], self.radius[idx]
        for centre, r in spheres:
            d = pts - centre
            dist = np.linalg.norm(d, axis=1)
            hit = (dist < r + rad) & (dist > 1e-9)
            pts[hit] += ((r + rad[hit] - dist[hit]) / dist[hit])[:, None] * d[hit]
        for a, b, r in capsules:
            ab = b - a
            t = np.clip(((pts - a) @ ab) / max(ab @ ab, 1e-12), 0, 1)
            closest = a + t[:, None] * ab
            d = pts - closest
            dist = np.linalg.norm(d, axis=1)
            hit = (dist < r + rad) & (dist > 1e-9)
            pts[hit] = closest[hit] + (d[hit] / dist[hit][:, None]) * (r + rad[hit])[:, None]
        loc[idx] = pts
        mark = self._lap("collisions", mark)
        self.link_lambda[:] = 0
        self.links_pass()
        mark = self._lap("links", mark)
        for level in self.levels:
            par = self.parent[level]
            bone = loc[level] - loc[par]
            length = np.linalg.norm(bone, axis=1, keepdims=True)
            bone_dir = bone / np.maximum(length, 1e-12)
            pose_dir = pose[level] - pose[par]
            pose_dir /= np.maximum(np.linalg.norm(pose_dir, axis=1, keepdims=True), 1e-12)
            cos = np.clip((bone_dir * pose_dir).sum(1), -1, 1)
            angle = np.arccos(cos)
            over = angle > self.limit[level]
            if over.any():
                # Rotate back onto the cone: slerp from the pose direction by the limit angle.
                lim = self.limit[level][over]
                ortho = bone_dir[over] - pose_dir[over] * cos[over][:, None]
                ortho /= np.maximum(np.linalg.norm(ortho, axis=1, keepdims=True), 1e-12)
                bone_dir[over] = pose_dir[over] * np.cos(lim)[:, None] + ortho * np.sin(lim)[:, None]
            rest_length = np.linalg.norm(pose[level] - pose[par], axis=1, keepdims=True)
            loc[level] = loc[par] + bone_dir * rest_length
        self._lap("angle limit + length", mark)

    def _lap(self, name, mark):
        now = time.perf_counter()
        self.phases[name] = self.phases.get(name, 0.0) + now - mark
        return now

    def write(self, pose):
        """Rotations from positions (ApplySimulateResult), as matrix_basis quaternions in one write.

        A bone with one child turns so its pose direction points at the child's
        simulated position: FindBetween(pose vector, simulated vector) * its pose
        rotation. Its local rotation is then taken against its parent's result.
        """
        real = self.real
        pose_rot = np.einsum("nij,njk->nik", self.root_pose[self.root_slot[:real]][:, :3, :3], self.rel_root)
        result = pose_rot.copy()
        a, c = self.aimed, self.aim_child
        turn = batch_rotation_between(pose[c] - pose[a], self.loc[c] - self.loc[a])
        result[a] = turn @ pose_rot[a]
        parent = self.parent[:real]
        parent_rot = np.where((parent >= 0)[:, None, None], result[np.maximum(parent, 0)],
                              self.collider_bones[self.root_parent_bone[self.root_slot[:real]]][:, :3, :3])
        local = np.einsum("nji,njk->nik", parent_rot @ self.rel_parent, result)
        self.rig.pose.bones.foreach_get("rotation_quaternion", self.quats)
        q = self.quats.reshape(self.count, 4)
        q[self.bone_of[:real]] = batch_quaternions(local).astype(np.float32)
        self.rig.pose.bones.foreach_set("rotation_quaternion", self.quats)
        self.rig.update_tag(refresh={"DATA"})

    def frame(self, first):
        t0 = time.perf_counter()
        pose = self.read_pose()
        t1 = time.perf_counter()
        if first:
            self.loc = pose.copy(); self.prev = pose.copy(); self.pose_prev = pose.copy()
        steps = 2 if (self.frames % 2) else 3  # 60 Hz steps at 24 fps: 2.5 a frame
        for k in range(steps):
            alpha = (k + 1) / steps
            self.substep(self.pose_prev + (pose - self.pose_prev) * alpha, STEP)
        self.pose_prev = pose
        self.last_pose = pose
        t2 = time.perf_counter()
        self.write(pose)
        t3 = time.perf_counter()
        return t1 - t0, t2 - t1, t3 - t2, steps


sims = [Sim(rig) for rig in bpy.data.objects if rig.type == "ARMATURE" and rig.get("ws_alpha_item")]
for sim in sims:
    sim.frames = 0
preview._set_sway(True)
timings = {sim.rig.name: [] for sim in sims}
for frame in range(1, 121):
    bpy.context.scene.frame_set(frame)
    for sim in sims:
        timings[sim.rig.name].append(sim.frame(frame == 1))
        sim.frames += 1
preview._set_sway(False)

for sim in sims:
    t = np.array([row[:3] for row in timings[sim.rig.name][10:]]) * 1000
    steps = np.array([row[3] for row in timings[sim.rig.name][10:]])
    print(f"SPIKE| {sim.rig['ws_alpha_item']}: per frame -- read {np.median(t[:, 0]):.3f} ms, "
          f"solve {np.median(t[:, 1]):.3f} ms ({np.median(t[:, 1] / steps):.3f} ms a substep), "
          f"write {np.median(t[:, 2]):.3f} ms; total {np.median(t.sum(1)):.3f} ms (median)")
    moving = sim.parent >= 0
    kept = np.linalg.norm(sim.loc[moving] - sim.loc[sim.parent[moving]], axis=1)
    posed = np.linalg.norm(sim.last_pose[moving] - sim.last_pose[sim.parent[moving]], axis=1)
    print(f"SPIKE|    sanity: all finite {np.isfinite(sim.loc).all()}, lengths match the pose to "
          f"{np.abs(kept - posed).max():.2e} m; posed lengths differ from rest by up to "
          f"{np.abs(posed - np.linalg.norm(sim.rest_heads[moving] - sim.rest_heads[sim.parent[moving]], axis=1)).max() * 1000:.2f} mm")
    substeps = sum(row[3] for row in timings[sim.rig.name])
    print("SPIKE|    per substep: " + ", ".join(f"{name} {total / substeps * 1000:.3f} ms"
                                               for name, total in sim.phases.items()))
total = sum(np.median(np.array([row[:3] for row in timings[sim.rig.name][10:]]).sum(1)) for sim in sims) * 1000
print(f"SPIKE| both rigs: {total:.3f} ms a frame at 24 fps (2.5 substeps a frame)")
