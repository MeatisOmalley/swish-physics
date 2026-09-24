"""Kawaii step over every rig in the scene at once: numpy vs the C DLL, one and five characters.

    blender -b --factory-startup --python kstep_bench.py

Every chain point of every rig lives in one set of arrays, sorted by chain depth so
parents come first. The numpy step works a depth at a time, runs links in batches
that share no point, and runs collisions one collider slot at a time across every
rig. The C step does the same work point by point in the same order, so the two
agree to rounding. Blender I/O -- reading pose matrices, writing rotations -- is
the same Python for both.
"""
import ctypes
import math
import os
import sys
import time

sys.path.insert(0, "C:/Users/meat/Documents/blender/code/vroid edits/vroid transfer")
import bpy
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
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

STEP = 1.0 / 60.0
COMPLIANCE = 0.000000001 / (STEP * STEP)   # Leather, over dt^2
F64, I32 = np.float64, np.int32


def rotation_between(u, v):
    u = u / np.linalg.norm(u, axis=1, keepdims=True)
    v = v / np.linalg.norm(v, axis=1, keepdims=True)
    axis = np.cross(u, v)
    sin = np.linalg.norm(axis, axis=1)
    cos = (u * v).sum(1)
    k = np.divide(axis, sin[:, None], out=np.zeros_like(axis), where=sin[:, None] > 1e-12)
    kx, ky, kz = k[:, 0], k[:, 1], k[:, 2]
    zero = np.zeros_like(kx)
    K = np.stack([np.stack([zero, -kz, ky], 1), np.stack([kz, zero, -kx], 1), np.stack([-ky, kx, zero], 1)], 1)
    return np.eye(3) + sin[:, None, None] * K + (1 - cos)[:, None, None] * (K @ K)


def quaternions(m):
    w = np.sqrt(np.maximum(0.0, 1 + m[:, 0, 0] + m[:, 1, 1] + m[:, 2, 2])) / 2
    x = np.copysign(np.sqrt(np.maximum(0.0, 1 + m[:, 0, 0] - m[:, 1, 1] - m[:, 2, 2])) / 2, m[:, 2, 1] - m[:, 1, 2])
    y = np.copysign(np.sqrt(np.maximum(0.0, 1 - m[:, 0, 0] + m[:, 1, 1] - m[:, 2, 2])) / 2, m[:, 0, 2] - m[:, 2, 0])
    z = np.copysign(np.sqrt(np.maximum(0.0, 1 - m[:, 0, 0] - m[:, 1, 1] + m[:, 2, 2])) / 2, m[:, 1, 0] - m[:, 0, 1])
    return np.stack([w, x, y, z], 1)


def unscaled(m):
    """Rotation part of matrices that carry scale: columns normalised."""
    return m / np.linalg.norm(m, axis=1, keepdims=True)


class Scene:
    """Every chain point of every rig, in one set of arrays."""

    def __init__(self, rigs):
        self.rigs = rigs
        self.bone_offset = np.cumsum([0] + [len(r.pose.bones) for r in rigs])
        self.bones_total = int(self.bone_offset[-1])
        self.matrix_buffers = [np.empty(len(r.pose.bones) * 16, dtype=np.float32) for r in rigs]
        self.quat_buffers = [np.empty(len(r.pose.bones) * 4, dtype=np.float32) for r in rigs]
        rows, roots_rows, shapes, links = [], [], [], []
        shape_start, shape_count = [], []
        for g, rig in enumerate(rigs):
            base = int(self.bone_offset[g])
            bones = rig.pose.bones
            index = {pb.name: i for i, pb in enumerate(bones)}
            first = len(rows)
            local_order = {}

            def add(pb, parent, depth, root):
                local_order[pb.name] = len(rows)
                rows.append(dict(name=pb.name, parent=parent, depth=depth, bone=base + index[pb.name], root=root,
                                 group=g, rest=np.array(pb.bone.matrix_local), tail=np.array(pb.bone.tail_local)))
                for child in pb.children:
                    if child.name.startswith("J_Sec"):
                        add(child, local_order[pb.name], depth + 1, root)

            for pb in bones:
                if pb.name.startswith("J_Sec") and not (pb.parent and pb.parent.name.startswith("J_Sec")):
                    roots_rows.append(len(rows))
                    add(pb, -1, 0, len(rows))
            last = len(rows)
            for i in range(first, last):
                if not any(r["parent"] == i for r in rows[first:last]):
                    rows.append(dict(name=rows[i]["name"] + " tip", parent=i, depth=rows[i]["depth"] + 1, bone=-1,
                                     root=rows[i]["root"], group=g, rest=None, tail=None, tip_of=i))
            prepared = springsim._prepare(rig, preview._spring_bones(rig))
            shape_start.append(len(shapes))
            for b, offset, radius, tail in sorted(prepared.colliders.values(), key=lambda c: c[3] is not None):
                shapes.append((base + b, np.array(offset), np.array(tail if tail is not None else offset), radius))
            shape_count.append(len(shapes) - shape_start[-1])
            links += self._skirt_ring(rows, first, len(rows))
        # Sort by depth, parents first; remap every index.
        order = sorted(range(len(rows)), key=lambda i: rows[i]["depth"])
        new = {old: k for k, old in enumerate(order)}
        rows = [rows[i] for i in order]
        for r in rows:
            r["parent"] = new[r["parent"]] if r["parent"] >= 0 else -1
            r["root"] = new[r["root"]]
            if "tip_of" in r:
                r["tip_of"] = new[r["tip_of"]]
        links = [(new[a], new[b]) for a, b in links]
        self.n = len(rows)
        self.parent = np.array([r["parent"] for r in rows], dtype=I32)
        self.group = np.array([r["group"] for r in rows], dtype=I32)
        self.bone = np.array([r["bone"] for r in rows])
        depth = np.array([r["depth"] for r in rows])
        self.levels = [slice(int(np.searchsorted(depth, d)), int(np.searchsorted(depth, d, "right")))
                       for d in range(1, depth.max() + 1)]
        self.roots = np.flatnonzero(self.parent < 0)
        root_slot = {int(r): k for k, r in enumerate(self.roots)}
        self.root_of = np.array([root_slot[r["root"]] for r in rows])
        rig_of_bone = np.searchsorted(self.bone_offset, np.arange(self.bones_total), "right") - 1
        # Chain roots ride their parent bone: pose = parent pose @ parent rest^-1 @ root rest.
        self.anchor_bone, anchor_fix = [], []
        for r in self.roots:
            row = rows[r]
            g = row["group"]
            pb = self.rigs[g].pose.bones[row["name"]]
            self.anchor_bone.append(int(self.bone_offset[g]) + list(self.rigs[g].pose.bones).index(pb.parent))
            anchor_fix.append(np.linalg.inv(np.array(pb.parent.bone.matrix_local)) @ row["rest"])
        self.anchor_bone = np.array(self.anchor_bone)
        self.anchor_fix = np.array(anchor_fix)
        # Every point's rest head in its chain root's rest space.
        local = []
        for r in rows:
            root_rest_inv = np.linalg.inv(rows[r["root"]]["rest"])
            head = rows[r["tip_of"]]["tail"] if "tip_of" in r else r["rest"][:3, 3]
            local.append((root_rest_inv @ np.append(head, 1.0))[:3])
        self.local = np.array(local)
        # Rotations for the write: bones only, relative to their root and to their parent at rest.
        self.real = np.flatnonzero(self.bone >= 0)
        self.rel_root = np.array([(np.linalg.inv(rows[rows[i]["root"]]["rest"]) @ rows[i]["rest"])[:3, :3]
                                  for i in self.real])
        rel_parent = []
        for i in self.real:
            r = rows[i]
            if r["parent"] >= 0:
                rel_parent.append((np.linalg.inv(rows[r["parent"]]["rest"]) @ r["rest"])[:3, :3])
            else:
                pb = self.rigs[r["group"]].pose.bones[r["name"]]
                rel_parent.append((np.linalg.inv(np.array(pb.parent.bone.matrix_local)) @ r["rest"])[:3, :3])
        self.rel_parent = np.array(rel_parent)
        slot = {int(i): k for k, i in enumerate(self.real)}
        self.real_parent_slot = np.array([slot.get(int(self.parent[i]), -1) for i in self.real])
        self.real_anchor = self.anchor_bone[self.root_of[self.real]]
        children = {}
        for i, p in enumerate(self.parent):
            children.setdefault(int(p), []).append(i)
        aimed = [i for i in self.real if len(children.get(int(i), [])) == 1]
        self.aimed_slot = np.array([slot[int(i)] for i in aimed])
        self.aimed = np.array(aimed)
        self.aim_child = np.array([children[int(i)][0] for i in aimed])
        # Settings per point.
        self.damping = np.full(self.n, 0.1)
        self.pull = np.full(self.n, 0.05)          # 1 - (1 - stiffness) ** (60 * dt), dt = 1/60
        self.radius = np.full(self.n, 0.02)
        self.limit = np.full(self.n, math.radians(60.0))
        self.world_loc = np.full(self.n, 0.8)
        self.world_rot = np.full(self.n, 0.8)
        self.gravity = np.array([0.0, 0.0, -9.81])
        self.move = np.zeros(3)
        self.move_rot = np.eye(3)
        # Colliders: by rig, spheres first then capsules (Kawaii's order); slots for numpy.
        self.shape_bone = np.array([sh[0] for sh in shapes])
        self.shape_offset_a = np.array([sh[1] for sh in shapes])
        self.shape_offset_b = np.array([sh[2] for sh in shapes])
        self.shape_radius = np.array([sh[3] for sh in shapes], dtype=F64)
        self.shape_start = np.array(shape_start, dtype=I32)
        self.shape_count = np.array(shape_count, dtype=I32)
        moving = np.flatnonzero(self.parent >= 0)
        self.slots = []
        for k in range(int(self.shape_count.max()) if len(shapes) else 0):
            pts = moving[self.shape_count[self.group[moving]] > k]
            self.slots.append((pts, self.shape_start[self.group[pts]] + k))
        # Links: batches that share no point; C runs them in the same order.
        batches = self._colour(links)
        self.link_batches = batches
        flat = [links[k] for batch in batches for k in batch]
        self.link_first = np.array([a for a, _b in flat], dtype=I32)
        self.link_second = np.array([b for _a, b in flat], dtype=I32)
        self.batch_slices, start = [], 0
        for batch in batches:
            self.batch_slices.append(slice(start, start + len(batch))); start += len(batch)
        self.lambda_ = np.zeros(len(flat))
        self.link_rest = None
        self.loc = self.prev = self.pose_prev = None
        print(f"BENCH| {len(rigs)} rigs: {self.n} points ({len(self.real)} bones), {len(self.roots)} chains, "
              f"depth {len(self.levels)}, {len(shapes)} colliders in {len(self.slots)} slots, "
              f"{len(flat)} links in {len(batches)} batches")

    @staticmethod
    def _skirt_ring(rows, first, last):
        roots = [i for i in range(first, last) if rows[i]["parent"] < 0 and "Skirt" in rows[i]["name"]]
        if len(roots) < 2:
            return []
        heads = {i: rows[i]["rest"][:3, 3] for i in roots}
        centre = np.mean([heads[i] for i in roots], axis=0)
        ring = sorted(roots, key=lambda i: math.atan2(heads[i][1] - centre[1], heads[i][0] - centre[0]))
        columns = []
        for r in ring:
            column, node = [r], r
            while True:
                kids = [i for i in range(first, last) if rows[i]["parent"] == node]
                if not kids:
                    break
                node = kids[0]; column.append(node)
            columns.append(column)
        out = []
        for a, b in zip(columns, columns[1:] + columns[:1]):
            out += [(a[d], b[d]) for d in range(1, min(len(a), len(b)))]
        return out

    @staticmethod
    def _colour(links):
        batches, left = [], list(range(len(links)))
        while left:
            used, batch, rest = set(), [], []
            for k in left:
                a, b = links[k]
                if a in used or b in used:
                    rest.append(k)
                else:
                    batch.append(k); used.update((a, b))
            batches.append(batch); left = rest
        return batches

    # --- Blender I/O
    def read(self):
        for rig, m, q in zip(self.rigs, self.matrix_buffers, self.quat_buffers):
            rig.pose.bones.foreach_get("matrix", m)
            rig.pose.bones.foreach_get("rotation_quaternion", q)
        self.mats = np.concatenate(self.matrix_buffers).reshape(-1, 4, 4).transpose(0, 2, 1).astype(F64)

    def pose(self):
        root_pose = self.mats[self.anchor_bone] @ self.anchor_fix
        self.root_pose = root_pose
        rp = root_pose[self.root_of]
        heads = np.einsum("nij,nj->ni", rp[:, :3, :3], self.local) + rp[:, :3, 3]
        a = self.mats[self.shape_bone]
        self.shape_a = np.einsum("nij,nj->ni", a[:, :3, :3], self.shape_offset_a) + a[:, :3, 3]
        self.shape_b = np.einsum("nij,nj->ni", a[:, :3, :3], self.shape_offset_b) + a[:, :3, 3]
        return heads

    def write(self, pose):
        """Rotations from positions (ApplySimulateResult), each bone's local rotation, one write per rig."""
        root_rot = unscaled(self.root_pose[:, :3, :3])
        pose_rot = np.einsum("nij,njk->nik", root_rot[self.root_of[self.real]], self.rel_root)
        result = pose_rot.copy()
        a, c = self.aimed, self.aim_child
        result[self.aimed_slot] = rotation_between(pose[c] - pose[a], self.loc[c] - self.loc[a]) @ pose_rot[self.aimed_slot]
        parent_rot = unscaled(self.mats[self.real_anchor][:, :3, :3])
        has = self.real_parent_slot >= 0
        parent_rot[has] = result[self.real_parent_slot[has]]
        local = np.einsum("nji,njk->nik", parent_rot @ self.rel_parent, result)
        q = quaternions(local).astype(np.float32)
        allq = np.concatenate(self.quat_buffers).reshape(-1, 4)
        allq[self.bone[self.real]] = q
        for g, (rig, buf) in enumerate(zip(self.rigs, self.quat_buffers)):
            buf[:] = allq[self.bone_offset[g]:self.bone_offset[g + 1]].ravel()
            rig.pose.bones.foreach_set("rotation_quaternion", buf)
            rig.update_tag(refresh={"DATA"})

    # --- the step, numpy
    def links_numpy(self):
        for sl in self.batch_slices:
            a, b = self.link_first[sl], self.link_second[sl]
            d = self.loc[b] - self.loc[a]
            length = np.sqrt((d * d).sum(1))
            ok = length > 0
            step = (length - self.link_rest[sl] - COMPLIANCE * self.lambda_[sl]) / (2.0 + COMPLIANCE)
            scale = np.where(ok, step / np.where(ok, length, 1.0), 0.0)
            self.loc[a] += d * scale[:, None]
            self.loc[b] -= d * scale[:, None]
            self.lambda_[sl] += np.where(ok, step, 0.0)

    def step_numpy(self, pose):
        loc, prev = self.loc, self.prev
        r = self.roots
        prev[r] = loc[r]; loc[r] = pose[r]
        m = self.levels[0].start
        v = (loc[m:] - prev[m:]) / STEP
        prev[m:] = loc[m:]
        v *= (1.0 - self.damping[m:])[:, None]
        v += self.gravity * STEP
        loc[m:] += v * STEP
        turned = prev[m:] @ self.move_rot.T
        loc[m:] += self.move * (1.0 - self.world_loc[m:])[:, None]
        loc[m:] += (turned - prev[m:]) * (1.0 - self.world_rot[m:])[:, None]
        for lv in self.levels:
            par = self.parent[lv]
            base = loc[par] + (pose[lv] - pose[par])
            loc[lv] += (base - loc[lv]) * self.pull[lv][:, None]
        self.lambda_[:] = 0
        self.links_numpy()
        for pts, k in self.slots:
            x = loc[pts]
            a, b = self.shape_a[k], self.shape_b[k]
            ab, ap = b - a, x - a
            abab = (ab * ab).sum(1)
            t = np.where(abab > 1e-12, (ap * ab).sum(1) / np.where(abab > 1e-12, abab, 1.0), 0.0)
            t = np.clip(t, 0.0, 1.0)
            c = a + t[:, None] * ab
            d = x - c
            dist = np.sqrt((d * d).sum(1))
            reach = self.shape_radius[k] + self.radius[pts]
            hit = (dist < reach) & (dist > 1e-9)
            x[hit] = c[hit] + d[hit] / dist[hit][:, None] * reach[hit][:, None]
            loc[pts] = x
        self.lambda_[:] = 0
        self.links_numpy()
        for lv in self.levels:
            par = self.parent[lv]
            bone = loc[lv] - loc[par]
            length = np.sqrt((bone * bone).sum(1))
            d = bone * (1.0 / np.maximum(length, 1e-12))[:, None]
            posed = pose[lv] - pose[par]
            posed_length = np.sqrt((posed * posed).sum(1))
            pd = posed * (1.0 / np.maximum(posed_length, 1e-12))[:, None]
            cos = np.clip((d * pd).sum(1), -1.0, 1.0)
            limit = self.limit[lv]
            over = (limit > 0) & (np.arccos(cos) > limit)
            if over.any():
                o = d[over] - pd[over] * cos[over][:, None]
                ol = np.maximum(np.sqrt((o * o).sum(1)), 1e-12)
                d[over] = pd[over] * np.cos(limit[over])[:, None] + o / ol[:, None] * np.sin(limit[over])[:, None]
            loc[lv] = loc[par] + d * posed_length[:, None]

    # --- the step, C
    def bind_c(self, dll):
        p = lambda a: a.ctypes.data_as(ctypes.c_void_p)
        dll.kstep.restype = None
        self._c = dll.kstep
        self._keep = [self.parent, self.damping, self.pull, self.radius, self.limit, self.gravity, self.move,
                      self.move_rot, self.world_loc, self.world_rot, self.link_first, self.link_second,
                      self.link_rest, self.lambda_, self.group, self.shape_start, self.shape_count, self.shape_radius]
        self._p = p

    def step_c(self, pose):
        p = self._p
        mr = np.ascontiguousarray(self.move_rot)
        self._c(ctypes.c_int(self.n), p(self.parent), p(self.loc), p(self.prev), p(pose),
                p(self.damping), p(self.pull), p(self.radius), p(self.limit), p(self.gravity),
                ctypes.c_double(STEP), ctypes.c_double(STEP), p(self.move), p(mr), p(self.world_loc), p(self.world_rot),
                ctypes.c_int(len(self.link_first)), p(self.link_first), p(self.link_second), p(self.link_rest),
                p(self.lambda_), ctypes.c_double(COMPLIANCE), ctypes.c_int(1), ctypes.c_int(1),
                p(self.group), p(self.shape_start), p(self.shape_count),
                p(self.shape_a), p(self.shape_b), p(self.shape_radius))

    def start(self, pose):
        self.loc = pose.copy(); self.prev = pose.copy(); self.pose_prev = pose.copy()
        self.link_rest = np.linalg.norm(pose[self.link_first] - pose[self.link_second], axis=1) \
            if len(self.link_first) else np.zeros(0)
        self.shape_a = np.ascontiguousarray(self.shape_a); self.shape_b = np.ascontiguousarray(self.shape_b)

    def frame(self, number, solver):
        t0 = time.perf_counter()
        self.read()
        t1 = time.perf_counter()
        pose = np.ascontiguousarray(self.pose())
        if self.loc is None:
            self.start(pose)
        t2 = time.perf_counter()
        steps = 3 if number % 2 else 2       # 60 Hz steps at 24 fps
        for k in range(steps):
            sub = np.ascontiguousarray(self.pose_prev + (pose - self.pose_prev) * ((k + 1) / steps))
            (self.step_numpy if solver == "numpy" else self.step_c)(sub)
        self.pose_prev = pose
        t3 = time.perf_counter()
        self.write(pose)
        t4 = time.perf_counter()
        return t1 - t0, t2 - t1, t3 - t2, t4 - t3, steps


dll = ctypes.CDLL(os.path.join(HERE, "kstep.dll"))
rigs = [o for o in bpy.data.objects if o.type == "ARMATURE" and o.get("ws_alpha_item")]


def run(label, rigs, solver, frames=120):
    scene = Scene(rigs)
    scene.bind_c(dll)
    preview._set_sway(True)
    rows = []
    for f in range(1, frames + 1):
        bpy.context.scene.frame_set(f)
        rows.append(scene.frame(f, solver))
    preview._set_sway(False)
    t = np.array([r[:4] for r in rows[10:]]) * 1000
    steps = np.array([r[4] for r in rows[10:]])
    print(f"BENCH| {label}, {solver}: read {np.median(t[:, 0]):.3f}, pose {np.median(t[:, 1]):.3f}, "
          f"solve {np.median(t[:, 2]):.3f} ({np.median(t[:, 2] / steps):.4f} a step), "
          f"write {np.median(t[:, 3]):.3f}; frame {np.median(t.sum(1)):.3f} ms")
    return scene


# Agreement: the same frames through both solvers, compared point by point.
a = Scene(rigs); a.bind_c(dll)
b = Scene(rigs); b.bind_c(dll)
preview._set_sway(True)
worst = 0.0
for f in range(1, 121):
    bpy.context.scene.frame_set(f)
    a.read(); b.read()
    pa = np.ascontiguousarray(a.pose()); pb = np.ascontiguousarray(b.pose())
    if a.loc is None:
        a.start(pa); b.start(pb)
    for k in range(2):
        a.step_numpy(pa); b.step_c(pb)
    worst = max(worst, float(np.abs(a.loc - b.loc).max()))
preview._set_sway(False)
print(f"BENCH| numpy and C agree to {worst:.2e} m over 240 steps")

run("1 character (2 rigs)", rigs, "numpy")
run("1 character (2 rigs)", rigs, "c")
copies = []
for n in range(4):
    for rig in rigs:
        copy = rig.copy()
        rig.users_collection[0].objects.link(copy)
        copies.append(copy)
bpy.context.view_layer.update()
run("5 characters (10 rigs)", rigs + copies, "numpy")
run("5 characters (10 rigs)", rigs + copies, "c")
