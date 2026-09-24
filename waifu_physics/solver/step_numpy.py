"""One Kawaii Physics substep in numpy: the reference implementation.

SimulateOnce (Simulation.cpp:799), in its order and its precision: positions
in doubles, settings and times in floats, and results narrowed to float where
Kawaii assigns them to a float. step.c does the same work point by point in
the same order and agrees with this to the bit.
"""
import math

import numpy as np

from . import uemath as ue
from .system import (KIND_BRIDGE, SPHERE_OUTER, SPHERE_INNER, CAPSULE, TAPERED, BOX, PLANE,
                     PLANAR_NONE)

F32, F64 = np.float32, np.float64
NAME = "numpy"


def pull(stiffness, exponent):
    """ApplyStiffnessPull's factor (Simulation.cpp:1308): 1 - (1 - Stiffness) ^ Exponent, in floats."""
    return (F32(1.0) - np.power(F32(1.0) - np.asarray(stiffness, dtype=F32), F32(exponent))).astype(F32)


def _f(values):
    """A float result used in double arithmetic, as C++ promotes it."""
    return np.asarray(values, dtype=F32).astype(F64)


def _over(values):
    """1 / scale for a vector division: TVector::operator/ multiplies by the reciprocal."""
    return 1.0 / np.asarray(values, dtype=F64)


def simulate_once(s):
    loc, prev, pose = s.loc, s.prev, s.pose
    # Roots are kinematic and follow the pose.
    roots = s.root_rows
    prev[roots] = loc[roots]
    loc[roots] = pose[roots]

    # Simulate (Simulation.cpp:1109) for every moving point. Integration and
    # world-movement follow depend only on the point, so they run for all at
    # once; the stiffness pull reads the parent's result, so it runs a depth
    # at a time, parents first -- the order Kawaii's bone loop gives.
    rows = s.sim_rows
    if len(rows):
        group = s.group[rows]
        dt_old = max(s.dt_old, ue.KINDA_SMALL)
        velocity = (loc[rows] - prev[rows]) * _over(F32(dt_old))
        prev[rows] = loc[rows]
        velocity = velocity * _f(F32(1.0) - s.damping[rows])[:, None]
        velocity = velocity + s.wind_vel[rows]                       # scene wind (zero when off)
        step_dt = F64(s.step_dt)
        legacy = s.legacy_gravity[group].astype(bool)
        gravity = s.gravity[group]
        velocity[~legacy] = velocity[~legacy] + gravity[~legacy] * step_dt
        for k in range(len(s.vforce)):                               # ApplyToVelocity, in force order
            m = s.vmask[k][rows].astype(bool)
            if m.any():
                velocity[m] = velocity[m] + s.vforce[k][rows[m]] * step_dt
        moved = loc[rows]
        moved[legacy] = moved[legacy] + ((0.5 * gravity[legacy]) * step_dt) * step_dt
        moved = moved + velocity * step_dt
        simple = s.simple_on[group].astype(bool)                     # ApplySimpleExternalForce
        if simple.any():
            moved[simple] = moved[simple] + s.simple_force[group[simple]] * step_dt
        follow = s.teleport[group] == 0
        if follow.any():
            f_rows = np.flatnonzero(follow)
            old = prev[rows[f_rows]]
            fg = group[f_rows]
            moved[f_rows] = moved[f_rows] + s.move[fg] * _f(F32(1.0) - s.world_damping_location[rows[f_rows]])[:, None]
            turned = ue.rotate_vector(s.move_rot[fg], old)
            moved[f_rows] = moved[f_rows] + (turned - old) * _f(F32(1.0) - s.world_damping_rotation[rows[f_rows]])[:, None]
        for k in range(len(s.pforce)):                               # external forces' Apply, in force order
            m = s.pmask[k][rows].astype(bool)
            if m.any():
                moved[m] = moved[m] + s.pforce[k][rows[m]] * step_dt
        loc[rows] = moved
        for level in s.sim_levels:
            par = s.parent[level]
            base = loc[par] + (pose[level] - pose[par])
            loc[level] = loc[level] + (base - loc[level]) * _f(s.pull[level])[:, None]

    # Inter-bone dummies ride between their bones; bridge dummies between their linked bones.
    if len(s.inter_rows):
        r = s.inter_rows
        prev[r] = loc[r]
        loc[r] = ue.lerp(loc[s.real_parent[r]], loc[s.real_child[r]], s.alpha[r])
    if len(s.bridge_rows):
        r = s.bridge_rows
        prev[r] = loc[r]
        loc[r] = ue.lerp(loc[s.real_parent[r]], loc[s.real_child[r]], s.alpha[r])
        s.bridge_base[r] = loc[r]

    _links(s, s.iterations_before)
    _collide(s)
    _bridge_feedback(s)
    _links(s, s.iterations_after)
    _limits(s)


def _links(s, iterations):
    """AdjustByBoneConstraints (Collision.cpp:1424), XPBD, λ reset before each group of iterations."""
    if not len(s.link_a):
        return
    counts = iterations[s.link_group]
    reset = counts > 0
    s.lambda_[reset] = F32(0.0)
    step_dt = max(s.step_dt, ue.KINDA_SMALL)
    loc = s.loc
    for it in range(int(counts.max(initial=0))):
        for batch in s.link_batches:
            k = np.arange(batch.start, batch.stop)[counts[batch] > it]
            if not len(k):
                continue
            a, b = s.link_a[k], s.link_b[k]
            delta = loc[b] - loc[a]
            length = ue.size(delta).astype(F32)
            ok = length > 0
            k, a, b, delta, length = k[ok], a[ok], b[ok], delta[ok], length[ok]
            constraint = length - s.link_length[k]
            compliance = s.link_compliance[k] / (step_dt * step_dt)
            step = (constraint - compliance * s.lambda_[k]) / (F32(2.0) + compliance)
            delta = (delta * _over(length)[:, None]) * step.astype(F64)[:, None]
            loc[a] = loc[a] + delta
            loc[b] = loc[b] - delta
            s.lambda_[k] = s.lambda_[k] + step


def _collide(s):
    """Collision passes: each point meets its group's shapes in Kawaii's order."""
    loc = s.loc
    for rows, shape in s.slots:
        kind = s.shape_type[shape]
        for which in np.unique(kind):
            m = kind == which
            r, sh = rows[m], shape[m]
            x = loc[r]
            radius = s.radius[r]
            if which in (SPHERE_OUTER, SPHERE_INNER):
                x = _sphere(s, x, radius, sh, which == SPHERE_OUTER)
            elif which == CAPSULE:
                x = _capsule(s, x, radius, sh)
            elif which == TAPERED:
                x = _tapered(s, x, radius, sh)
            elif which == BOX:
                x = _box(s, x, radius, sh)
            elif which == PLANE:
                x = _plane(s, x, radius, sh, s.prev[r])
            loc[r] = x


def _sphere(s, x, radius, sh, outer):
    """AdjustBySphereCollision (Collision.cpp:1082)."""
    centre = s.shape_loc[sh]
    delta = x - centre
    dist_sq = ue.size_squared(delta).astype(F32)      # const float DistSq
    out = x.copy()
    if outer:
        limit = s.shape_radius0[sh] + radius
        hit = ~(dist_sq > limit * limit)
        dist = np.sqrt(dist_sq)                        # sqrtf
        push = hit & (dist > ue.KINDA_SMALL)
        out[push] = x[push] + (delta[push] * _over(dist[push])[:, None]) * _f(limit[push] - dist[push])[:, None]
    else:
        limit = np.maximum(s.shape_radius0[sh] - radius, F32(0.0))
        hit = ~(dist_sq < limit * limit)
        dist = np.sqrt(dist_sq)
        away = hit & (dist > ue.KINDA_SMALL)
        out[away] = centre[away] + (delta[away] * _over(dist[away])[:, None]) * _f(limit[away])[:, None]
        at_centre = hit & ~(dist > ue.KINDA_SMALL)
        out[at_centre] = centre[at_centre]
    return out


def _closest_on_segment(x, start, end):
    """FMath::ClosestPointOnSegment."""
    segment = end - start
    dot1 = ue.dot(x - start, segment)
    dot2 = ue.dot(segment, segment)
    ratio = np.divide(dot1, dot2, out=np.zeros_like(dot1), where=dot2 != 0)
    closest = start + segment * ratio[:, None]
    closest = np.where((dot2 <= dot1)[:, None], end, closest)
    return np.where((dot1 <= 0)[:, None], start, closest)


def _push_out(x, closest, limit, fallback):
    direction = ue.safe_normal(x - closest)
    zero = ue.nearly_zero(direction)
    direction[zero] = fallback[zero]
    return closest + direction * _f(limit)[:, None]


def _capsule(s, x, radius, sh):
    """AdjustByCapsuleCollision (Collision.cpp:1161)."""
    start, end = s.shape_start_point[sh], s.shape_end_point[sh]
    closest = _closest_on_segment(x, start, end)
    dist_sq = ue.size_squared(x - closest).astype(F32)
    limit = radius + s.shape_radius0[sh]
    hit = dist_sq < limit * limit
    out = x.copy()
    if hit.any():
        out[hit] = _push_out(x[hit], closest[hit], limit[hit], s.shape_fallback_dir[sh][hit])
    return out


def _tapered(s, x, radius, sh):
    """AdjustByTaperedCapsuleCollision (Collision.cpp:1189)."""
    fallback = s.shape_fallback[sh].astype(bool)
    closest = s.shape_fallback_center[sh].copy()
    tapered_radius = s.shape_fallback_radius[sh].copy()
    seg = ~fallback
    if seg.any():
        start, segment = s.shape_start_point[sh][seg], s.shape_segment[sh][seg]
        t = np.clip(ue.dot(x[seg] - start, segment) / s.shape_segment_sq[sh][seg], 0.0, 1.0).astype(F32)
        closest[seg] = start + segment * _f(t)[:, None]
        r0, r1 = s.shape_radius0[sh][seg], s.shape_radius1[sh][seg]
        tapered_radius[seg] = np.maximum(r0 + t * (r1 - r0), F32(0.0))
    limit = radius + tapered_radius
    dist_sq = ue.size_squared(x - closest).astype(F32)
    hit = dist_sq < limit * limit
    out = x.copy()
    if hit.any():
        out[hit] = _push_out(x[hit], closest[hit], limit[hit], s.shape_fallback_dir[sh][hit])
    return out


def _box(s, x, radius, sh):
    """AdjustByBoxCollision (Collision.cpp:1234)."""
    rot, centre, extent = s.shape_rot[sh], s.shape_loc[sh], s.shape_extent[sh]
    local = ue.unrotate_vector(rot, x - centre) * 1.0
    r = _f(radius)
    clamped = np.clip(local, -extent, extent)
    outside = np.where(local < -extent, local + extent, np.where(local > extent, local - extent, 0.0))
    dist_sq = outside[:, 0] * outside[:, 0]
    dist_sq = dist_sq + outside[:, 1] * outside[:, 1]
    dist_sq = dist_sq + outside[:, 2] * outside[:, 2]
    touching = dist_sq <= r * r
    out = x.copy()
    if not touching.any():
        return out
    push = local - clamped
    distance = ue.size(push).astype(F32)
    inside = np.all((local >= -extent) & (local <= extent), axis=1) | (distance <= ue.KINDA_SMALL)
    new_local = local.copy()
    moved = np.zeros(len(x), dtype=bool)
    inner = touching & inside
    if inner.any():
        penetration = extent[inner] - np.abs(local[inner])
        px, py, pz = penetration[:, 0], penetration[:, 1], penetration[:, 2]
        axis_index = np.where((px <= py) & (px <= pz), 0, np.where(py <= pz, 1, 2))
        rows = np.flatnonzero(inner)
        sign = np.where(local[rows, axis_index] >= 0.0, 1.0, -1.0)
        new_local[rows, axis_index] = sign * (extent[rows, axis_index] + r[rows])
        moved[rows] = True
    outer = touching & ~inside & (distance <= radius)
    if outer.any():
        direction = ue.safe_normal(push[outer])
        new_local[outer] = clamped[outer] + direction * r[outer][:, None]
        moved |= outer
    if moved.any():
        out[moved] = ue.rotate_vector(rot[moved], new_local[moved] * 1.0) + centre[moved]
    return out


def _plane(s, x, radius, sh, previous):
    """AdjustByPlanarCollision (Collision.cpp:1339)."""
    normal, w = s.shape_normal[sh], s.shape_plane_w[sh]
    plane_dot = ue.dot(normal, x) - w
    on_plane = x - normal * plane_dot[:, None]
    dist_sq = ue.size_squared(x - on_plane).astype(F32)
    with np.errstate(divide="ignore", invalid="ignore"):
        t = ((w - ue.dot(x, normal)) / ue.dot(previous - x, normal)).astype(F32)
    crosses = (t > -ue.KINDA_SMALL) & (t < F32(1.0) + ue.KINDA_SMALL)
    hit = (dist_sq < radius * radius) | crosses
    out = x.copy()
    out[hit] = on_plane[hit] + normal[hit] * _f(radius[hit])[:, None]
    return out


def _bridge_feedback(s):
    """Bridge dummies hand their collision push to the bones they sit between (Simulation.cpp:995)."""
    rows = s.bridge_feedback_rows
    if not len(rows):
        return
    push = s.loc[rows] - s.bridge_base[rows]
    live = ~ue.nearly_zero(push)
    rows, push = rows[live], push[live]
    s.push_sum[:] = 0.0
    s.push_weight[:] = 0.0
    if len(rows):
        alpha = s.alpha[rows]
        w1, w2 = F32(1.0) - alpha, alpha
        ends = np.stack([s.real_parent[rows], s.real_child[rows]], axis=1).ravel()
        values = np.stack([push * _f(w1)[:, None], push * _f(w2)[:, None]], axis=1).reshape(-1, 3)
        weights = np.stack([w1, w2], axis=1).ravel()
        np.add.at(s.push_sum, ends, values)
        np.add.at(s.push_weight, ends, weights)
        hit = np.flatnonzero(s.push_weight > 0)
        scale = _f(s.feedback_scale[s.group[hit]])
        divisor = _f(np.maximum(F32(1.0), s.push_weight[hit]))
        s.loc[hit] = s.loc[hit] + (s.push_sum[hit] * _over(divisor)[:, None]) * scale[:, None]


def _limits(s):
    """Angle limit, planar constraint and length restore, parents first (Simulation.cpp:1064)."""
    loc, pose = s.loc, s.pose
    for level in s.limit_levels:
        par = s.parent[level]
        limit = s.limit_angle[level]
        limited = np.flatnonzero(limit != 0.0)
        if len(limited):
            r, p = level[limited], par[limited]
            bone_dir = ue.safe_normal(loc[r] - loc[p])
            pose_dir = ue.safe_normal(pose[r] - pose[p])
            axis = ue.cross(pose_dir, bone_dir)
            angle = ue.atan2(ue.size(axis), ue.dot(pose_dir, bone_dir)).astype(F32)
            over = angle * ue.RAD_TO_DEG - limit[limited]
            beyond = np.flatnonzero(over > 0)
            if len(beyond):
                r, p = r[beyond], p[beyond]
                rotation_axis = ue.safe_normal(axis[beyond])
                zero = ue.nearly_zero(rotation_axis)
                if zero.any():
                    rotation_axis[zero] = ue.axis(s.pose_rot[p[zero]], 0)
                turned = ue.rotate_angle_axis(bone_dir[beyond], -over[beyond].astype(F64), rotation_axis)
                loc[r] = turned * ue.size(loc[r] - loc[p])[:, None] + loc[p]
        axis_index = s.planar_axis[s.group[level]]
        planar = np.flatnonzero(axis_index != PLANAR_NONE)
        if len(planar):
            r, p = level[planar], par[planar]
            normal = np.empty((len(r), 3))
            for k in (1, 2, 3):
                m = axis_index[planar] == k
                if m.any():
                    normal[m] = ue.axis(s.pose_rot[p[m]], k - 1)
            w = ue.dot(loc[p], normal)
            loc[r] = loc[r] - normal * (ue.dot(normal, loc[r]) - w)[:, None]
        bone_length = ue.size(pose[level] - pose[par]).astype(F32)
        loc[level] = ue.safe_normal(loc[level] - loc[par]) * _f(bone_length)[:, None] + loc[par]
