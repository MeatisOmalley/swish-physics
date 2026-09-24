"""Blender's force fields, as the chains feel them: a port of the physical effectors in Blender 5.2
(blenkernel/intern/effect.cc: get_effector_data, effector_falloff, do_physical_effector, BKE_effectors_apply).

Kawaii has nothing like them; this is the add-on's Blender-only part, so the Kawaii step is left alone and
gets the result as a per-point acceleration. Points are taken as particles are (pd_point_from_particle):
velocity in units a second, and the sum a force on a mass of 1, so an acceleration in units a second squared.

Wind, Force, Vortex, Magnetic, Harmonic, Turbulence and Drag, with Point, Plane and Line shapes, sphere, tube
and cone falloff, distance limits, Z direction, flow and noise. Charge and Lennard-Jones act between charged
or sized particles, which chain points are not, so they give nothing, as in Blender. Surface and Every Point
shapes, Texture, Curve Guide, Boid and Fluid Flow fields are not followed."""
import math
from dataclasses import dataclass

import numpy as np

SUPPORTED = ("WIND", "FORCE", "VORTEX", "MAGNET", "HARMONIC", "TURBULENCE", "DRAG")
SHAPES = ("POINT", "PLANE", "LINE")
_EPSILON = 1.1920929e-07                     # FLT_EPSILON


@dataclass
class FieldSpec:
    """One field object, in world space: what effect.cc reads from its PartDeflect and matrix."""
    kind: str
    location: np.ndarray                     # the object's world location
    axis: np.ndarray                         # its world Z axis, normalized
    strength: float = 1.0                    # f_strength (Drag: its quadratic drag)
    damping: float = 0.0                     # f_damp (Harmonic: damping; Drag: linear drag)
    flow: float = 0.0
    noise: float = 0.0
    seed: int = 0
    shape: str = "POINT"
    falloff: str = "SPHERE"
    power: float = 0.0
    use_min: bool = False
    min_distance: float = 0.0
    use_max: bool = False
    max_distance: float = 0.0
    radial_power: float = 0.0
    use_min_radial: bool = False
    min_radial: float = 0.0
    use_max_radial: bool = False
    max_radial: float = 0.0
    z_direction: str = "BOTH"
    gravitation: bool = False                # Force: multiply by 1 / distance squared
    size: float = 0.0                        # f_size (Harmonic: rest length; Turbulence: noise size)
    global_coordinates: bool = False         # Turbulence: noise in world space, not the field's
    weight: float = 1.0                      # the effector weight: the group's Strength


def relevant(spec):
    """is_effector_nonzero_strength: a field whose settings give it no effect is skipped."""
    if spec.strength != 0.0 or spec.noise > 0.0 or spec.flow != 0.0:
        return True
    if spec.kind == "VORTEX":
        return spec.shape != "POINT"
    if spec.kind == "DRAG":
        return spec.damping != 0.0
    return False


class _Random:
    """Blender's RandomNumberGenerator (BLI_rand.hh): drand48's generator."""

    def __init__(self, seed):
        self.x = ((seed & 0xFFFFFFFF) << 16) | 0x330E

    def int32(self):
        self.x = (0x5DEECE66D * self.x + 0xB) & 0x0000FFFFFFFFFFFF
        return (self.x >> 17) & 0x7FFFFFFF

    def float(self):
        return np.float32(self.int32()) / np.float32(0x80000000)


def _wind_func(rng, strength):
    random = (rng.int32() + 1) % 128
    force = rng.float() + np.float32(1.0)
    sign = 1.0 if float(random) > 64.0 else -1.0
    return float(np.float32(sign) * (np.float32(random) / force) * np.float32(strength) / np.float32(128.0))


def _falloff_func(fac, use_min, min_distance, use_max, max_distance, power):
    fac = np.asarray(fac, dtype=np.float64)
    low = min_distance if use_min else 0.0
    result = np.power(1.0 + fac - low, -power)
    if use_min:
        result = np.where(fac < min_distance, 1.0, result)
    if use_max:
        result = np.where(fac > max_distance, 0.0, result)
    return result


def _normalized(v):
    length = np.linalg.norm(v, axis=-1, keepdims=True)
    return np.divide(v, length, out=np.zeros_like(v), where=length > 0.0), length[..., 0]


def _turbulence(size, x, y, z):
    """BLI_noise_generic_turbulence(size, x, y, z, 2 octaves, soft, improved Perlin), via mathutils.noise,
    whose noise() is 2 * that unsigned noise - 1."""
    from mathutils import noise
    scale = 1.0 / size if size != 0.0 else 1.0
    x, y, z = x * scale, y * scale, z * scale
    total, amp, fscale = 0.0, 1.0, 1.0
    for _octave in range(3):
        value = noise.noise((fscale * x, fscale * y, fscale * z), noise_basis="PERLIN_NEW")
        total += (value + 1.0) * 0.5 * amp
        amp, fscale = amp * 0.5, fscale * 2.0
    return total * (4.0 / 7.0)


def accelerations(specs, positions, velocities, frame):
    """The fields' summed acceleration on each point (world units a second squared), for world-space positions
    and velocities (units a second), on this frame (it seeds the fields' noise)."""
    positions = np.asarray(positions, dtype=np.float64)
    velocities = np.asarray(velocities, dtype=np.float64)
    total = np.zeros_like(positions)
    for spec in specs:
        if spec.kind in SUPPORTED and spec.shape in SHAPES and relevant(spec):
            total += _one(spec, positions, velocities, frame)
    return total


def _one(spec, p, vel, frame):
    n = len(p)
    nor = np.asarray(spec.axis, dtype=np.float64)
    origin = np.asarray(spec.location, dtype=np.float64)

    # get_effector_data: the field's point nearest each point, for its shape
    to_center = p - origin
    if spec.shape in ("PLANE", "LINE"):
        translate = np.outer(to_center @ nor, nor)
        loc = origin + translate if spec.kind == "VORTEX" or spec.shape == "LINE" else p - translate
    else:
        loc = np.broadcast_to(origin, p.shape)
    vec_to_point = p - loc
    distance = np.linalg.norm(vec_to_point, axis=1)
    if spec.kind == "HARMONIC" and spec.size:
        with np.errstate(invalid="ignore", divide="ignore"):
            ratio = np.where(distance > 0.0, (distance - spec.size) / distance, 0.0)
        vec_to_point = vec_to_point * ratio[:, None]
    vec_to_point2, nor2 = to_center, nor

    # effector_falloff
    falloff = np.full(n, spec.weight, dtype=np.float64)
    fac = vec_to_point2 @ nor
    blocked = (fac < 0.0) if spec.z_direction == "POSITIVE" else (fac > 0.0) if spec.z_direction == "NEGATIVE" \
        else np.zeros(n, dtype=bool)
    dist = (spec.use_min, spec.min_distance, spec.use_max, spec.max_distance, spec.power)
    radial = (spec.use_min_radial, spec.min_radial, spec.use_max_radial, spec.max_radial, spec.radial_power)
    if spec.falloff == "SPHERE":
        falloff = falloff * _falloff_func(distance, *dist)
    elif spec.falloff == "TUBE":
        falloff = falloff * _falloff_func(np.abs(fac), *dist)
        across = np.linalg.norm(vec_to_point2 - np.outer(fac, nor), axis=1)
        falloff = np.where(falloff == 0.0, 0.0, falloff * _falloff_func(across, *radial))
    else:                                                               # CONE
        falloff = falloff * _falloff_func(np.abs(fac), *dist)
        length = np.linalg.norm(vec_to_point2, axis=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            angle = np.degrees(np.arccos(np.clip(np.where(length > 0.0, fac / length, 1.0), -1.0, 1.0)))
        falloff = np.where(falloff == 0.0, 0.0, falloff * _falloff_func(angle, *radial))
    falloff = np.where(blocked, 0.0, falloff)
    live = falloff > 0.0

    # noise: Blender draws it per point, from a generator seeded with the field's seed and the frame
    strength = np.full(n, spec.strength, dtype=np.float64)
    damp = np.full(n, spec.damping, dtype=np.float64)
    if spec.noise > 0.0:
        rng = _Random(spec.seed + int(abs(frame)))
        for i in np.flatnonzero(live):
            strength[i] += _wind_func(rng, spec.noise)
            if spec.kind in ("HARMONIC", "DRAG"):
                damp[i] += _wind_func(rng, spec.noise)

    # do_physical_effector
    sf = (strength * falloff)[:, None]
    kind = spec.kind
    if kind == "WIND":
        force = np.broadcast_to(nor, p.shape) * sf
    elif kind == "FORCE":
        force, _ = _normalized(vec_to_point)
        if spec.gravitation:
            with np.errstate(divide="ignore"):
                strength = np.where(distance < _EPSILON, 0.0, strength * np.power(distance, -2.0))
            sf = (strength * falloff)[:, None]
        force = force * sf
    elif kind == "VORTEX":
        if spec.shape == "POINT":
            force, _ = _normalized(np.cross(nor, vec_to_point))
            force = force * sf * distance[:, None]
        else:
            temp = np.cross(nor2, vec_to_point2) * sf
            force = np.cross(nor2, temp) * sf + temp - vel
    elif kind == "MAGNET":
        temp = np.cross(nor, vec_to_point) if spec.shape in ("POINT", "LINE") else np.broadcast_to(nor, p.shape)
        temp, _ = _normalized(temp)
        force = np.cross(vel, temp * sf)
    elif kind == "HARMONIC":
        force = vec_to_point * -sf + vel * (-damp * 2.0 * np.sqrt(np.abs(strength)))[:, None]
    elif kind == "TURBULENCE":
        temp = p if spec.global_coordinates else vec_to_point2 + nor2
        force = np.array([[2.0 * _turbulence(spec.size, a, b, c) - 1.0,
                           2.0 * _turbulence(spec.size, b, c, a) - 1.0,
                           2.0 * _turbulence(spec.size, c, a, b) - 1.0] for a, b, c in temp]).reshape(-1, 3)
        force = force * sf
    else:                                                               # DRAG, as effect.cc writes it
        direction, speed = _normalized(vel)
        strength, damp = np.minimum(strength, 2.0), np.minimum(damp, 2.0)
        force = direction * (-falloff * speed * (strength * speed + damp))[:, None]

    force = np.where(live[:, None], force, 0.0)
    if kind not in ("HARMONIC", "DRAG") and spec.flow != 0.0:
        force = force + vel * (-spec.flow * np.where(live, falloff, 0.0))[:, None]
    return force
