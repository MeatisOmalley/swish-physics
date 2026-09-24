"""What Kawaii evaluates once a frame, before its substeps: external forces, wind, sync bones.

Kawaii's external forces (ExternalForces/*.cpp) prepare themselves in
PreApply, once per SimulateModifyBones, then add to each bone in every
substep. Everything they add is fixed for the frame -- a vector per bone,
scaled by the substep's dt -- so Waifu Physics evaluates them here, in Python, into
per-point arrays, and the step (numpy or C) only adds `vector * dt` at
Kawaii's place in Simulate():

    velocity  += scene wind                       (ComputeVerletStepVelocity, after damping)
    velocity  += velocity forces * dt             (ApplyToVelocity: Gravity), after gravity
    location  += simple external force * dt       (ApplySimpleExternalForce), after integration
    location  += position forces * dt             (Apply: Basic, Curve, Wind, Procedural Wind),
                                                   after world-movement follow, before the pull

Sync bones (KawaiiPhysicsSyncBone.cpp, AnimNode_KawaiiPhysicsSyncBone.cpp)
move pose targets once a frame, before warm-up and simulation.

Randomness. Kawaii draws RandomForceScaleRange, the scene wind's gust and the
Wind force's direction noise from Unreal's global random stream, which no
two runs share. Waifu Physics draws them from Unreal's FRandomStream seeded by the
frame number, so a frame simulates the same every time (the cache and
renders depend on it). Procedural wind is seeded in Kawaii too, and matches
it exactly.

Arithmetic follows Kawaii's types: vectors in doubles, settings and times in
floats, float trigonometry from the C runtime (Unreal's FMath::Sin is sinf).
"""
import ctypes
import math
import sys
from dataclasses import dataclass, field

import numpy as np

from . import uemath as ue

F32, F64 = np.float32, np.float64
KINDA_SMALL = F32(1.0e-4)
UE_PI_F = F32(3.1415926535897932)
TWO_PI_F = F32(UE_PI_F * F32(2.0))
DEG_TO_RAD_F = F32(UE_PI_F / F32(180.0))       # FMath::DegreesToRadians(float): DegVal * (UE_PI / 180.f)

KIND_BONE, KIND_TIP, KIND_INTER, KIND_BRIDGE = 0, 1, 2, 3
BASIC, GRAVITY, CURVE, WIND, PROCEDURAL_WIND = "BASIC", "GRAVITY", "CURVE", "WIND", "PROCEDURAL_WIND"
COMPONENT, WORLD, BONE = "COMPONENT", "WORLD", "BONE"
SINGLE, AVERAGE, MAX, MIN = "SINGLE", "AVERAGE", "MAX", "MIN"
BOTH, POSITIVE, NEGATIVE, NONE = "BOTH", "POSITIVE", "NEGATIVE", "NONE"

# --------------------------------------------------------------------------- float math


def _crt():
    if sys.platform != "win32":
        return None
    try:
        crt = ctypes.CDLL("ucrtbase")
        for name in ("sinf", "cosf", "acosf"):
            getattr(crt, name).restype = ctypes.c_float
            getattr(crt, name).argtypes = [ctypes.c_float]
        return crt
    except OSError:
        return None


_CRT = _crt()


def sinf(x):
    """FMath::Sin(float): the C runtime's sinf, as Unreal calls it (correctly rounded sin elsewhere)."""
    if _CRT is not None:
        return F32(_CRT.sinf(float(x)))
    return F32(math.sin(float(x)))


def cosf(x):
    if _CRT is not None:
        return F32(_CRT.cosf(float(x)))
    return F32(math.cos(float(x)))


def acosf(x):
    if _CRT is not None:
        return F32(_CRT.acosf(float(x)))
    return F32(math.acos(max(-1.0, min(1.0, float(x)))))


def deg_to_rad_f(degrees):
    return F32(F32(degrees) * DEG_TO_RAD_F)


def lerp_f(a, b, alpha):
    """FMath::Lerp(float, float, float): A + Alpha * (B - A)."""
    a, b, alpha = F32(a), F32(b), F32(alpha)
    return F32(a + F32(alpha * F32(b - a)))


def fmod_f(a, b):
    """FMath::Fmod on floats; fmod is exact, so the double result is the float result."""
    return F32(math.fmod(float(a), float(b)))


def _safe_normal(v):
    return ue.safe_normal(np.asarray(v, dtype=F64)[None])[0]


def _nearly_zero(v, tolerance=KINDA_SMALL):
    return bool(np.all(np.abs(np.asarray(v, dtype=F64)) <= float(tolerance)))


# --------------------------------------------------------------------------- Unreal's random streams


class RandomStream:
    """FRandomStream: a linear congruential generator; FRand builds a float in [0, 1) from its bits."""

    def __init__(self, seed):
        self.seed = int(seed) & 0xFFFFFFFF

    def frand(self):
        self.seed = (self.seed * 196314165 + 907633515) & 0xFFFFFFFF
        bits = 0x3F800000 | (self.seed >> 9)
        return F32(np.frombuffer(np.uint32(bits).tobytes(), dtype=F32)[0] - F32(1.0))

    def frand_range(self, low, high):
        low, high = F32(low), F32(high)
        return F32(low + F32(F32(high - low) * self.frand()))

    def vrand(self):
        """FMath::VRand: a uniform unit vector by rejection."""
        while True:
            v = np.array([F32(self.frand() * F32(2.0) - F32(1.0)) for _ in range(3)], dtype=F64)
            length = float(v @ v)
            if KINDA_SMALL <= length <= 1.0:
                return v * (1.0 / math.sqrt(length))

    def vrand_cone(self, direction, half_angle):
        """FMath::VRandCone: a direction within half_angle (radians) of direction."""
        direction = np.asarray(direction, dtype=F64)
        if half_angle <= 0.0:
            return _safe_normal(direction)
        theta = F32(F32(F32(2.0) * UE_PI_F) * self.frand())
        phi = acosf(F32(F32(F32(2.0) * self.frand()) - F32(1.0)))
        phi = fmod_f(phi, half_angle)
        base = _safe_normal(direction)
        side = np.cross(base, [0.0, 0.0, 1.0] if abs(base[2]) < 0.99 else [1.0, 0.0, 0.0])
        side = _safe_normal(side)
        tilted = ue.rotate_angle_axis(base[None], np.array([float(phi) * 180.0 / math.pi]), side[None])[0]
        turned = ue.rotate_angle_axis(tilted[None], np.array([float(theta) * 180.0 / math.pi]), base[None])[0]
        return _safe_normal(turned)


def stable_hash(seed, grid_index, channel):
    """ComputeStableHash (ProceduralWind.cpp:916): FNV-1a with an fmix32-style finish."""
    h = 0x811C9DC5
    for value in (seed, grid_index, channel):
        h ^= int(value) & 0xFFFFFFFF
        h = (h * 0x01000193) & 0xFFFFFFFF
    h ^= h >> 16
    h = (h * 0x7FEB352D) & 0xFFFFFFFF
    h ^= h >> 15
    h = (h * 0x846CA68B) & 0xFFFFFFFF
    h ^= h >> 16
    return h


def noise_at(grid_index, seed, channel):
    """SampleNoiseAt: a value in [-1, 1) for a grid point."""
    stream = RandomStream(stable_hash(seed, grid_index, channel))
    return stream.frand_range(-1.0, 1.0)


def smooth_noise(u, seed, channel=0):
    """SampleSmoothNoise: value noise between grid points with a smoothstep."""
    u = F32(u)
    grid = F32(math.floor(u))
    index = int(grid)
    alpha = F32(u - grid)
    smooth = F32(F32(alpha * alpha) * F32(F32(3.0) - F32(F32(2.0) * alpha)))
    return lerp_f(noise_at(index, seed, channel), noise_at(index + 1, seed, channel), smooth)


# --------------------------------------------------------------------------- specifications


@dataclass
class ForceSpec:
    """One FKawaiiPhysics_ExternalForce, in Kawaii's units (centimetres, seconds, degrees)."""
    kind: str
    enabled: bool = True
    space: str = WORLD
    apply_bones: frozenset = frozenset()          # ApplyBoneFilter by bone name; empty for every bone
    ignore_bones: frozenset = frozenset()
    random_range: tuple = (1.0, 1.0)              # RandomForceScaleRange
    rate_curve: object = None                     # ForceRateByBoneLengthRate: LinearCurve or None
    params: dict = field(default_factory=dict)


@dataclass
class SyncTargetSpec:
    root: int                                     # sorted point index of the target bone, or -1
    include_children: bool = True
    rate_curve: object = None                     # ScaleCurveByBoneLengthRate


@dataclass
class SyncSpec:
    """One FKawaiiPhysicsSyncBone. Locations are this frame's and the rest pose's, in component space."""
    location: object = None                       # None when the source bone is missing
    rest_location: object = None
    targets: list = field(default_factory=list)
    global_scale: tuple = (1.0, 1.0, 1.0)
    distance_curve: object = None                 # ScaleCurveByDeltaDistance
    directions: tuple = (BOTH, BOTH, BOTH)
    attenuation: bool = False
    inner_radius: float = 0.0
    outer_radius: float = 0.0
    max_attenuation: float = 1.0


# --------------------------------------------------------------------------- frame context


class FrameContext:
    """What a group's forces read in PreApply."""

    def __init__(self, system, group, rows, rng, frame_dt, wind):
        s = system
        self.system, self.group, self.rows, self.rng = s, group, rows, rng
        self.step_dt = F32(frame_dt)                                # GetStepDeltaTime() outside substeps
        self.length_rate = s.length_rate[rows]
        self.names = [s.bone_names[i] for i in rows]
        self.world_to_sim = s.world_to_sim[group]
        self.wind = wind                                            # (world direction, speed) or None

    def to_sim(self, vector, space):
        """ConvertExternalForceToSimulationSpace: bone space stays bone-local."""
        vector = np.asarray(vector, dtype=F64)
        if space == WORLD:
            return self.world_to_sim @ vector
        return vector

    def bone_tm(self, vectors):
        """FTransform::TransformVector with each point's bone transform: rotate (scale * v)."""
        s = self.system
        tm = s.tm_point[self.rows]
        scaled = np.asarray(vectors, dtype=F64) * s.frame_pose_scale[tm]
        return ue.rotate_vector(s.frame_pose_rot[tm], scaled)

    def can_apply(self, spec):
        """FKawaiiPhysics_ExternalForce::CanApply; dummies have no bone name."""
        mask = np.ones(len(self.rows), dtype=bool)
        if spec.apply_bones:
            mask &= np.array([bool(name) and name in spec.apply_bones for name in self.names], dtype=bool)
        if spec.ignore_bones:
            mask &= ~np.array([bool(name) and name in spec.ignore_bones for name in self.names], dtype=bool)
        return mask

    def rate(self, spec, default=1.0):
        if spec.rate_curve is None:
            return np.full(len(self.rows), F32(default), dtype=F32)
        return spec.rate_curve.many(self.length_rate)


# --------------------------------------------------------------------------- forces


class Force:
    """A force's per-frame state. pre_apply returns ("velocity" | "position", vectors, mask) for the rows."""
    supports_random_scale = True

    def __init__(self, spec):
        self.spec = spec
        self.time = F32(0.0)

    def randomized_scale(self, ctx):
        """FKawaiiPhysics_ExternalForce::PreApply: RandRange(Min, Max)."""
        if not self.supports_random_scale:
            return F32(1.0)
        low, high = self.spec.random_range
        return ctx.rng.frand_range(low, high)

    def state(self):
        return (self.time,)

    def set_state(self, state):
        (self.time,) = state


class BasicForce(Force):
    def pre_apply(self, ctx):
        spec, p = self.spec, self.spec.params
        scale = self.randomized_scale(ctx)
        self.time = F32(self.time + ctx.step_dt)
        interval = F32(p.get("interval", 0.0))
        direction = np.asarray(p.get("direction", (0.0, 0.0, 0.0)), dtype=F64)
        if interval > 0.0:
            if self.time > interval:
                force = direction * F64(scale)
                self.time = fmod_f(self.time, interval)
            else:
                force = np.zeros(3)
        else:
            force = direction * F64(scale)
        force = ctx.to_sim(force, spec.space)
        return "position", _positioned(ctx, spec, force), ctx.can_apply(spec)


class GravityForce(Force):
    def pre_apply(self, ctx):
        spec, p = self.spec, self.spec.params
        scale = self.randomized_scale(ctx)
        force = (_safe_normal(p.get("direction", (0.0, 0.0, -1.0))) if p.get("override_direction")
                 else np.array([0.0, 0.0, -1.0]))
        force = ctx.world_to_sim @ (force * F64(scale))
        rate = ctx.rate(spec, 1.0).astype(F64)
        return "velocity", force[None] * rate[:, None], ctx.can_apply(spec)


class CurveForce(Force):
    def _value(self, t):
        curves = self.spec.params.get("curves", (None, None, None))
        return np.array([0.0 if c is None else float(c.many([t], default=F32(0.0))[0]) for c in curves])

    def pre_apply(self, ctx):
        spec, p = self.spec, self.spec.params
        scale = self.randomized_scale(ctx)
        time_scale = F32(p.get("time_scale", 1.0))
        max_time = F32(p.get("max_time", 0.0))
        mode = p.get("evaluate", SINGLE)
        if mode == SINGLE:
            self.time = F32(self.time + F32(ctx.step_dt * time_scale))
            if max_time > 0 and self.time > max_time:
                self.time = fmod_f(self.time, max_time)
            force = self._value(self.time) * F64(scale)
        else:
            steps = max(int(p.get("substeps", 10)), 1)
            sub = F32(F32(ctx.step_dt * time_scale) / F32(steps))
            values = []
            for _ in range(steps):
                self.time = F32(self.time + sub)
                if max_time > 0 and self.time > max_time:
                    self.time = fmod_f(self.time, max_time)
                values.append(self._value(self.time))
            if mode == AVERAGE:
                force = np.zeros(3)
                for value in values:
                    force = force + value
                force = force * (1.0 / F64(F32(steps)))
            elif mode == MAX:
                force = np.full(3, float(np.finfo(F32).min))
                for value in values:
                    force = np.maximum(force, value)
            else:
                force = np.full(3, float(np.finfo(F32).max))
                for value in values:
                    force = np.minimum(force, value)
            force = force * F64(scale)
        force = ctx.to_sim(force, spec.space)
        return "position", _positioned(ctx, spec, force), ctx.can_apply(spec)


class WindForce(Force):
    """FKawaiiPhysics_ExternalForce_Wind: the scene's wind, per bone, noise drawn once a frame."""

    def pre_apply(self, ctx):
        spec, p = self.spec, self.spec.params
        scale = self.randomized_scale(ctx)
        n = len(ctx.rows)
        mask = ctx.can_apply(spec)
        vectors = np.zeros((n, 3))
        if ctx.wind is None:
            return "position", vectors, np.zeros(n, dtype=bool)
        world_direction, speed = ctx.wind
        direction = ctx.world_to_sim @ np.asarray(world_direction, dtype=F64)
        noise = F32(p.get("noise_angle", 0.0))
        rate = ctx.rate(spec)
        for k in np.flatnonzero(mask):
            d = ctx.rng.vrand_cone(direction, deg_to_rad_f(noise)) if noise > 0 else direction
            vectors[k] = ((d * F64(F32(speed))) * F64(rate[k])) * F64(scale)
        mask &= F32(speed) > KINDA_SMALL
        return "position", vectors, mask


class ProceduralWindForce(Force):
    """FKawaiiPhysics_ExternalForce_ProceduralWind, local source, no gust requests."""
    supports_random_scale = False

    def __init__(self, spec):
        super().__init__(spec)
        self.unscaled_time = F32(0.0)

    def state(self):
        return (self.time, self.unscaled_time)

    def set_state(self, state):
        self.time, self.unscaled_time = state

    def sample(self, t, length_rate=0.0):
        """ComputeWindSample: (constant, sway, ripple, strength cycle, random, gust, total)."""
        p = self.spec.params
        t = F32(t)
        sway_period = max(F32(p.get("sway_period", 1.0)), F32(0.01))
        ripple_period = max(F32(p.get("ripple_period", 1.0)), F32(0.01))
        random_period = max(F32(p.get("random_period", 0.5)), F32(0.01))
        cycle_period = max(F32(p.get("cycle_period", 10.0)), F32(0.01))
        constant = F32(p.get("constant", 0.0))
        sway = F32(F32(p.get("sway", 0.0)) * sinf(
            F32(F32(F32(TWO_PI_F * t) / sway_period) + deg_to_rad_f(p.get("sway_phase", 0.0)))))
        ripple = self.ripple(t, length_rate)
        cycle = lerp_f(p.get("cycle_min", 1.0), p.get("cycle_max", 1.0), F32(F32(0.5) * F32(F32(1.0) + sinf(
            F32(F32(F32(TWO_PI_F * t) / cycle_period) + deg_to_rad_f(p.get("cycle_phase", 0.0)))))))
        random = F32(F32(p.get("random", 0.0)) * smooth_noise(F32(t / random_period), int(p.get("seed", 0)), 0))
        gust = F32(0.0)
        total = F32(F32(F32(F32(F32(constant + sway) + ripple) * cycle) + random) + gust)
        return constant, sway, ripple, cycle, random, gust, total

    def ripple(self, t, length_rate):
        p = self.spec.params
        period = max(F32(p.get("ripple_period", 1.0)), F32(0.01))
        phase = F32(F32(F32(TWO_PI_F * F32(t)) / period)
                    - deg_to_rad_f(F32(F32(length_rate) * F32(p.get("ripple_delay", 180.0)))))
        phase = F32(phase + deg_to_rad_f(p.get("ripple_phase", 0.0)))
        return F32(F32(p.get("ripple", 0.0)) * sinf(phase))

    def direction(self):
        """The wind's direction this frame, with its seeded cone noise (PreApply)."""
        p = self.spec.params
        base = _safe_normal(p.get("direction", (1.0, 0.0, 0.0)))
        if _nearly_zero(base):
            base = np.array([1.0, 0.0, 0.0])
        angle = F32(p.get("noise_angle", 0.0))
        if angle <= 0.0:
            return base
        period = max(F32(p.get("noise_period", 1.0)), F32(0.01))
        u = F32(self.time / period)
        seed = int(p.get("seed", 0))
        return cone_noise(base, smooth_noise(u, seed, 1), smooth_noise(u, seed, 2), angle)

    def pre_apply(self, ctx):
        spec, p = self.spec, self.spec.params
        dt = max(ctx.step_dt, F32(0.0))
        self.time = F32(self.time + F32(dt * F32(p.get("time_scale", 1.0))))
        self.unscaled_time = F32(self.unscaled_time + dt)
        constant, sway, _ripple, cycle, random, gust, _total = self.sample(self.time, 0.0)
        sines = F32(constant + sway)
        wind = ctx.to_sim(self.direction(), spec.space)
        rate = ctx.rate(spec)
        totals = np.array([F32(F32(F32(F32(sines + self.ripple(self.time, r)) * cycle) + random) + gust)
                           for r in ctx.length_rate], dtype=F32)
        if spec.space == BONE:
            base = ctx.bone_tm(np.broadcast_to(wind, (len(ctx.rows), 3)))
        else:
            base = np.broadcast_to(wind, (len(ctx.rows), 3))
        vectors = (base * totals.astype(F64)[:, None]) * rate.astype(F64)[:, None]
        return "position", vectors, ctx.can_apply(spec)


def cone_noise(direction, noise_x, noise_y, half_angle_degrees):
    """ApplyConeNoiseToDirection (ProceduralWind.cpp:81)."""
    half = deg_to_rad_f(max(F32(half_angle_degrees), F32(0.0)))
    base = _safe_normal(direction)
    if _nearly_zero(base):
        base = np.array([1.0, 0.0, 0.0])
    if half <= KINDA_SMALL:
        return base
    disk = np.array([float(noise_x), float(noise_y), 0.0])
    length = F32(math.sqrt(disk[0] * disk[0] + disk[1] * disk[1]))
    if length <= KINDA_SMALL:
        return base
    if length > 1.0:
        disk = disk * (1.0 / F64(length))
    axis_x, axis_y = best_axis_vectors(base)
    offset = _safe_normal(axis_x * disk[0] + axis_y * disk[1])
    angle = F32(half * min(length, F32(1.0)))
    return _safe_normal(base * F64(cosf(angle)) + offset * F64(sinf(angle)))


def best_axis_vectors(v):
    """TVector::FindBestAxisVectors."""
    nx, ny, nz = abs(v[0]), abs(v[1]), abs(v[2])
    axis1 = np.array([1.0, 0.0, 0.0]) if (nz > nx and nz > ny) else np.array([0.0, 0.0, 1.0])
    axis1 = _safe_normal(axis1 - v * float(axis1 @ v))
    return axis1, np.cross(axis1, v)


def _positioned(ctx, spec, force):
    """Apply's per-bone vector before * dt: force (bone-transformed in bone space) * ForceRate."""
    rate = ctx.rate(spec).astype(F64)
    if spec.space == BONE:
        return ctx.bone_tm(np.broadcast_to(force, (len(ctx.rows), 3))) * rate[:, None]
    return force[None] * rate[:, None]


FIELDS = "FIELDS"


class FieldsForce(Force):
    """Blender's force fields (solver/fields.py), which Kawaii has no part of: the runtime works out each
    point's acceleration for the frame, and it is added through velocity like Gravity."""
    supports_random_scale = False

    def pre_apply(self, ctx):
        vectors = np.asarray(self.spec.params["vectors"], dtype=F64)
        return "velocity", vectors, np.ones(len(ctx.rows), dtype=bool)


FORCES = {BASIC: BasicForce, GRAVITY: GravityForce, CURVE: CurveForce, WIND: WindForce,
          PROCEDURAL_WIND: ProceduralWindForce, FIELDS: FieldsForce}


def make(spec):
    return FORCES[spec.kind](spec)


# --------------------------------------------------------------------------- scene wind


def scene_wind_velocity(ctx, settings, gust, noise_rotation, target_framerate):
    """GetWindVelocity (Simulation.cpp:1317) * TargetFramerate, the same for every point
    (Waifu Physics' wind sources are directional)."""
    scale = F32(settings.get("wind_scale", 1.0))
    if scale == 0.0 or ctx.wind is None:
        return np.zeros(3)
    world_direction, speed = ctx.wind
    direction = ctx.world_to_sim @ np.asarray(world_direction, dtype=F64)
    if F32(settings.get("wind_noise_angle", 0.0)) > 0:
        direction = ue.rotate_vector(noise_rotation[None], direction[None])[0]
    velocity = ((direction * F64(F32(speed))) * F64(scale)) * F64(gust)
    return velocity * F64(int(target_framerate))


def scene_wind_noise(rng, noise_angle):
    """The gust factor and cone rotation Kawaii draws once a frame."""
    gust = rng.frand_range(0.0, 2.0)
    if F32(noise_angle) > 0:
        axis = rng.vrand()
        angle = rng.frand_range(0.0, deg_to_rad_f(noise_angle))
        rotation = ue.quat_from_axis_angle(axis, float(angle))
    else:
        rotation = np.array([0.0, 0.0, 0.0, 1.0])
    return gust, rotation


# --------------------------------------------------------------------------- sync bones


def _attenuation(spec, distance):
    if not spec.attenuation:
        return F32(1.0)
    inner, outer, top = F32(spec.inner_radius), F32(spec.outer_radius), F32(spec.max_attenuation)
    outer = max(outer, inner)
    if distance <= inner:
        amount = F32(0.0)
    elif distance >= outer:
        amount = top
    else:
        denom = F32(outer - inner)
        t = F32(F32(distance - inner) / denom) if denom > KINDA_SMALL else F32(1.0)
        amount = F32(t * top)
    return max(F32(0.0), F32(F32(1.0) - amount))


def _passes(value, direction):
    return (direction == BOTH or (direction == POSITIVE and value > 0.0)
            or (direction == NEGATIVE and value < 0.0))


def sync_targets(system, target):
    """CollectSyncBoneChildTargets: [(point, scale)] for a target root and, optionally, its children."""
    s = system
    root = target.root
    if root < 0:
        return []
    scale_of = (lambda rate: F32(target.rate_curve(F32(rate)))) if target.rate_curve is not None else None
    found = [(root, F32(1.0))]
    if not target.include_children:
        return found          # the root's curve scale is only set when children are collected (game builds)
    start = F32(s.length_from_root[root])
    longest = start
    children, stack = [], list(s.child_lists[root])
    while stack:
        i = stack.pop()
        kind = s.kind[i]
        if not (kind == KIND_INTER or (kind == KIND_TIP and s.real_parent[i] >= 0)):
            children.append(i)
            longest = max(longest, F32(s.length_from_root[i]))
        stack.extend(s.child_lists[i])
    span = F32(longest - start)
    rates = [F32(F32(F32(s.length_from_root[i]) - start) / span) if span > KINDA_SMALL else F32(0.0)
             for i in children]
    if scale_of is not None:
        found = [(root, scale_of(0.0))]
        return found + [(i, scale_of(r)) for i, r in zip(children, rates)]
    return found + [(i, F32(1.0)) for i in children]


def apply_sync_target(system, i, translation, scale):
    """FKawaiiPhysicsSyncTarget::Apply: move a pose target, keeping its length to its parent."""
    s = system
    if s.skip_known and s.skip[i]:
        return
    pose = s.frame_pose
    moved = translation * F64(scale)
    parent = s.parent[i]
    if parent < 0:
        pose[i] = pose[i] + moved
        return
    new = pose[i] + moved
    length = F32(s.bone_length[i])
    if s.kind[parent] == KIND_INTER:
        one_minus = F32(F32(1.0) - s.alpha[parent])
        grand = s.real_parent[parent]
        if one_minus > KINDA_SMALL and grand >= 0:
            anchor, length = pose[grand], F32(length / one_minus)
        else:
            pose[i] = new
            return
    else:
        anchor = pose[parent]
    direction = _safe_normal(new - anchor)
    if _nearly_zero(direction):
        direction = _safe_normal(pose[i] - anchor)
    pose[i] = new if _nearly_zero(direction) else direction * F64(length) + anchor


def apply_sync(system, spec):
    """ApplySyncBones for one sync bone."""
    if spec.location is None or spec.rest_location is None:
        return
    s = system
    location = np.asarray(spec.location, dtype=F64)
    delta = location - np.asarray(spec.rest_location, dtype=F64)
    if spec.distance_curve is not None:
        length = math.sqrt(delta[0] * delta[0] + delta[1] * delta[1] + delta[2] * delta[2])
        delta = delta * F64(spec.distance_curve(F32(length)))
    delta = delta * np.asarray(spec.global_scale, dtype=F64)
    delta = np.array([delta[a] if _passes(delta[a], spec.directions[a]) else 0.0 for a in range(3)])
    if _nearly_zero(delta):
        return
    for target in spec.targets:
        for i, scale in sync_targets(s, target):
            d = s.loc[i] - location
            distance = F32(math.sqrt(d[0] * d[0] + d[1] * d[1] + d[2] * d[2]))
            apply_sync_target(s, i, delta * F64(_attenuation(spec, distance)), scale)
