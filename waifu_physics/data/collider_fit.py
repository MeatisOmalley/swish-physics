"""Fitting a collider to the skin around a bone.

The points are the skin that belongs to one bone (colliders.skin_points), in the bone's rest frame: Y along the
bone, the head at the origin, in armature units. Each shape is fitted to them and scored by how far the points lie
from its surface, on average; Auto keeps the best, a more complex shape winning only when clearly better.
Shapes sit a little inside the skin (the 75th percentile), leaving room for the chains' own collision radius.

Shapes as the collider modifier takes them (colliders.py): capsules along local Y with Length between the end
spheres' centres, a tapered capsule's Radius at +Y and Radius 1 at -Y, a box by half extents."""
from dataclasses import dataclass, field

import numpy as np

FITTED = ("Sphere", "Capsule", "Tapered Capsule", "Box")
DEPTH = 75.0                     # percentile of the skin's distance the surface is set at
MIN_POINTS = 12                  # fewer than this and nothing is fitted
INSETS = np.linspace(0.0, 1.0, 5)  # how far, in radii, a capsule's end sphere may sit inside the skin's end
# How much better than the simplest fit a more complex shape must be to be chosen by Auto.
COMPLEXITY = {"Sphere": 1.0, "Capsule": 1.0, "Tapered Capsule": 1.1, "Box": 1.2}


@dataclass
class Fit:
    shape: str
    center: np.ndarray = field(default_factory=lambda: np.zeros(3))
    radius: float = 0.0
    radius1: float = 0.0
    length: float = 0.0
    extent: np.ndarray = field(default_factory=lambda: np.zeros(3))
    error: float = float("inf")


def _score(distances):
    """How far the points lie from the surface, on average. Every shape is fitted to the same points, so the
    raw distance compares them fairly."""
    return float(np.mean(np.abs(distances)))


def sphere(points):
    lo, hi = np.percentile(points, 5, axis=0), np.percentile(points, 95, axis=0)
    center = (lo + hi) / 2.0
    d = np.linalg.norm(points - center, axis=1)
    radius = float(np.percentile(d, DEPTH))
    return Fit("Sphere", center=center, radius=radius, radius1=radius, error=_score(d - radius))


def _axis(points):
    """The capsule's axis: along the bone (Y), through the skin's middle across it."""
    x = (np.percentile(points[:, 0], 5) + np.percentile(points[:, 0], 95)) / 2.0
    z = (np.percentile(points[:, 2], 5) + np.percentile(points[:, 2], 95)) / 2.0
    radial = np.hypot(points[:, 0] - x, points[:, 2] - z)
    return x, z, radial


def _along_bone(name, points, x, z, radial, r_low, r_high):
    """A capsule from r_low at -Y to r_high at +Y around the axis. Each end sphere sits at the skin's end where
    the skin is open there (a limb going on into the next bone, which the capsule's cap then overlaps), up to its
    radius inside where the skin closes (a fingertip, the top of the head): whichever fits the skin better."""
    y = points[:, 1]
    lo, hi = np.percentile(y, 3), np.percentile(y, 97)
    best = None
    for low_in in INSETS:
        for high_in in INSETS:
            y0, y1 = lo + low_in * r_low, hi - high_in * r_high
            if y1 < y0:
                y0 = y1 = (y0 + y1) / 2.0
            s = np.clip((y - y0) / max(y1 - y0, 1.0e-9), 0.0, 1.0)
            along = y - np.clip(y, y0, y1)
            error = _score(np.hypot(radial, along) - (r_low + s * (r_high - r_low)))
            if best is None or error < best[0]:
                best = (error, y0, y1)
    error, y0, y1 = best
    return Fit(name, center=np.array([x, (y0 + y1) / 2.0, z]), radius=r_high, radius1=r_low, length=y1 - y0,
               error=error)


def capsule(points):
    x, z, radial = _axis(points)
    radius = float(np.percentile(radial, DEPTH))
    return _along_bone("Capsule", points, x, z, radial, radius, radius)


def tapered_capsule(points):
    x, z, radial = _axis(points)
    y = points[:, 1]
    lo, hi = np.percentile(y, 3), np.percentile(y, 97)
    third = (hi - lo) / 3.0
    low_part, high_part = radial[y <= lo + third], radial[y >= hi - third]
    r_low = float(np.percentile(low_part if len(low_part) else radial, DEPTH))
    r_high = float(np.percentile(high_part if len(high_part) else radial, DEPTH))
    return _along_bone("Tapered Capsule", points, x, z, radial, r_low, r_high)


def box(points):
    lo, hi = np.percentile(points, 5, axis=0), np.percentile(points, 95, axis=0)
    center, half = (lo + hi) / 2.0, np.maximum((hi - lo) / 2.0, 1.0e-6)
    q = np.abs(points - center) - half
    outside = np.linalg.norm(np.maximum(q, 0.0), axis=1)
    inside = np.minimum(q.max(axis=1), 0.0)
    return Fit("Box", center=center, extent=half, error=_score(outside + inside))


FITS = {"Sphere": sphere, "Capsule": capsule, "Tapered Capsule": tapered_capsule, "Box": box}


def fit(points, shape="AUTO"):
    """The collider for these points: the shape asked for, or with AUTO the best fitting one. None when there
    are too few points to fit (the caller falls back to one sized from the bone)."""
    points = np.asarray(points, dtype=np.float64)
    if len(points) < MIN_POINTS:
        return None
    if shape != "AUTO":
        return FITS[shape if shape in FITS else "Sphere"](points)
    fits = [FITS[name](points) for name in FITTED]
    return min(fits, key=lambda found: found.error * COMPLEXITY[found.shape])
