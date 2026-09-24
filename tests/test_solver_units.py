"""The step's parts, against results worked out by hand."""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import check, finish, REPO

sys.path.insert(0, REPO)
import numpy as np
from swish_physics.solver import native, uemath as ue
from swish_physics.solver.system import (System, Group, Shape, SPHERE_OUTER, SPHERE_INNER, CAPSULE, TAPERED,
                                         BOX, PLANE, PLANAR_X, COMPLIANCE_TYPES)

F32 = np.float32
backend = native.backend()
DT = F32(1.0) / F32(60.0)


def chain(points, rotation=(0.0, 0.0, 0.0, 1.0), extra=None):
    """One chain through the given points, the first fixed."""
    n = len(points)
    data = dict(parent=[i - 1 for i in range(n)], group=[0] * n, kind=[0] * n, real_parent=[-1] * n,
                real_child=[-1] * n, alpha=[0.0] * n, location=points, pose=points,
                pose_rotation=[rotation] * n, length_rate=[i / max(n - 1, 1) for i in range(n)])
    return data


def system(points, shapes=(), gravity=(0.0, 0.0, -980.0), **settings):
    values = dict(damping=0.0, stiffness=0.0, world_damping_location=1.0, world_damping_rotation=1.0,
                  radius=1.0, limit_angle=0.0)
    values.update(settings)
    s = System([Group(settings=values)], chain(points), {})
    s.gravity[0] = gravity
    if shapes:
        s.set_shapes([list(shapes)])
    s.resolve_settings()
    return s


def run(s, frames):
    for _ in range(frames):
        s.step_frame(DT, backend)


# --- integration and damping
s = system([(0.0, 0.0, 0.0), (10.0, 0.0, 0.0)], damping=1.0)
s.step_frame(DT, backend)
fall = (980.0 * float(DT)) * float(DT)
expected = np.array([10.0, 0.0, -fall]) / math.hypot(10.0, fall) * 10.0
check("full damping: one step moves the point by g dt^2, then back to its length",
      np.allclose(s.loc[1], expected, atol=1e-12), (s.loc[1], expected))

s = system([(0.0, 0.0, 0.0), (10.0, 0.0, 0.0)])
lowest, lengths = 0.0, []
for _ in range(60):
    run(s, 1)
    lowest = min(lowest, s.loc[1, 2])
    lengths.append(np.linalg.norm(s.loc[1] - s.loc[0]))
check("an undamped pendulum swings through the bottom", lowest < -9.9, lowest)
check("... keeping its length every frame", max(abs(l - 10.0) for l in lengths) < 1e-6, max(lengths))

swings = {}
for damping in (0.0, 0.2):
    s = system([(0.0, 0.0, 0.0), (10.0, 0.0, 0.0)], damping=damping)
    run(s, 240)
    swings[damping] = abs(s.loc[1, 0])
check("damping shrinks the swing", swings[0.2] < swings[0.0], swings)

# --- stiffness
s = system([(0.0, 0.0, 0.0), (0.0, 0.0, -10.0)], stiffness=1.0, gravity=(500.0, 0.0, 0.0))
run(s, 3)
check("stiffness 1 holds the pose against gravity", np.allclose(s.loc[1], s.pose[1], atol=1e-9), s.loc[1])
s = system([(0.0, 0.0, 0.0), (0.0, 0.0, -10.0)], stiffness=0.3, gravity=(0.0, 0.0, 0.0))
s.loc[1] = (10.0, 0.0, 0.0)
s.prev[1] = (10.0, 0.0, 0.0)
run(s, 60)
check("stiffness pulls a displaced point back to the pose", np.linalg.norm(s.loc[1] - s.pose[1]) < 0.05,
      s.loc[1])

# --- colliders: a point falling onto each shape ends up on its surface, not inside it
point = [(0.0, 0.0, 0.0), (0.0, 0.0, -10.0)]
cases = {
    "sphere": (Shape(SPHERE_OUTER, location=(0.0, 0.0, -14.0), radius=3.0),
               lambda p: np.linalg.norm(p - (0, 0, -14)) - 3.0),
    "capsule": (Shape(CAPSULE, location=(0.0, 0.0, -14.0), rotation=(0.0, 0.7071067811865476, 0.0, 0.7071067811865476),
                      radius=3.0, length=20.0),
                lambda p: math.hypot(p[1], p[2] + 14.0) - 3.0),
    "tapered capsule": (Shape(TAPERED, location=(0.0, 0.0, -14.0), rotation=(0.0, 0.7071067811865476, 0.0,
                                                                              0.7071067811865476),
                              radius=2.0, radius1=4.0, length=20.0),
                        None),
    "box": (Shape(BOX, location=(0.0, 0.0, -14.0), extent=(20.0, 20.0, 2.0)),
            lambda p: (p[2] - (-12.0))),
    "plane": (Shape(PLANE, location=(0.0, 0.0, -5.0)), lambda p: p[2] - (-5.0)),
}
for name, (shape, gap) in cases.items():
    s = system(point, [shape], gravity=(0.0, 0.0, -980.0), damping=0.1)
    # Hanging straight down for most shapes; for the plane, released from the side so
    # the tip swings down onto it while it can still rest there (10 cm bone, plane 5 cm down).
    s.loc[1] = (10.0, 0.0, 0.0) if name == "plane" else (0.5, 0.2, -10.0)
    s.prev[1] = s.loc[1]
    run(s, 120)
    if gap is None:
        # Radius tapers along X from 2 (+X end at x=10) to 4 (-X end at x=-10), so 3 at x=0.
        t = min(max((10.0 - s.loc[1, 0]) / 20.0, 0.0), 1.0)
        surface = 2.0 + t * 2.0
        distance = math.hypot(s.loc[1, 1], s.loc[1, 2] + 14.0) - surface
    else:
        distance = gap(s.loc[1])
    check(f"a point rests on the {name}, one point radius clear", distance > 1.0 - 1e-4, (distance, s.loc[1]))

s = system([(0.0, 0.0, 0.0), (0.0, 0.0, -2.0)], [Shape(SPHERE_INNER, location=(0.0, 0.0, 0.0), radius=6.0)],
           gravity=(0.0, 0.0, -980.0), radius=1.0)
run(s, 60)
check("an inner sphere keeps a point inside, a radius from its wall",
      np.linalg.norm(s.loc[1]) <= 5.0 + 1e-4, np.linalg.norm(s.loc[1]))

# --- angle limit
s = system([(0.0, 0.0, 0.0), (0.0, 0.0, -10.0)], gravity=(2000.0, 0.0, 0.0), limit_angle=30.0)
run(s, 120)
direction = (s.loc[1] - s.loc[0]) / np.linalg.norm(s.loc[1] - s.loc[0])
angle = math.degrees(math.acos(max(-1.0, min(1.0, direction @ np.array([0.0, 0.0, -1.0])))))
check("the angle limit holds a bone within 30 degrees of its pose", angle <= 30.0 + 1e-4, angle)

# --- planar constraint: points stay in the plane through the parent, normal to its X axis
s = System([Group(settings=dict(damping=0.0, stiffness=0.0, world_damping_location=1.0,
                                world_damping_rotation=1.0, radius=1.0, limit_angle=0.0),
                  planar_constraint=PLANAR_X)],
           chain([(0.0, 0.0, 0.0), (0.0, 0.0, -10.0), (0.0, 0.0, -20.0)]), {})
s.gravity[0] = (700.0, 300.0, -980.0)
s.resolve_settings()
run(s, 60)
check("the planar constraint keeps the chain in its plane", np.abs(s.loc[:, 0]).max() < 1e-9, s.loc[:, 0])

# --- XPBD links: stiffer materials hold their rest length more closely
gaps = {}
for material in ("CONCRETE", "MUSCLE", "FAT"):
    # Two hanging bones 10 apart, their tips linked with a rest length of 5.
    places = [(0.0, 0.0, 0.0), (0.0, 0.0, -10.0), (10.0, 0.0, 0.0), (10.0, 0.0, -10.0)]
    points = dict(parent=[-1, 0, -1, 2], group=[0] * 4, kind=[0] * 4, real_parent=[-1] * 4, real_child=[-1] * 4,
                  alpha=[0.0] * 4, location=places, pose=places, pose_rotation=[(0.0, 0.0, 0.0, 1.0)] * 4,
                  length_rate=[0.0, 1.0, 0.0, 1.0])
    links = dict(a=[1], b=[3], length=[5.0])
    # Stiffness holds the tips 10 apart; the link wants 5. How far it gets depends on its compliance.
    s = System([Group(settings=dict(damping=0.3, stiffness=0.5, world_damping_location=1.0,
                                    world_damping_rotation=1.0, radius=1.0, limit_angle=0.0),
                      compliance_type=COMPLIANCE_TYPES.index(material))], points, links)
    s.resolve_settings()
    run(s, 120)
    gaps[material] = abs(np.linalg.norm(s.unsorted(s.loc)[3] - s.unsorted(s.loc)[1]) - 5.0)
check("a link pulls its points toward its rest length, stiffer materials closer",
      gaps["CONCRETE"] < gaps["MUSCLE"] < gaps["FAT"], gaps)

# --- component movement and teleport
moved = {}
for teleport in (0, 1):
    s = system([(0.0, 0.0, 0.0), (0.0, 0.0, -10.0)], gravity=(0.0, 0.0, 0.0), world_damping_location=0.0)
    s.frame_move[0] = (5.0, 0.0, 0.0)
    s.teleport[0] = teleport
    s.step_frame(DT, backend)
    moved[teleport] = s.loc[1, 0]
check("component movement carries points (world damping 0)", moved[0] > 1.0, moved)
check("... except in a teleported frame", moved[1] == 0.0, moved)

# --- rotations from positions
s = system([(0.0, 0.0, 0.0), (0.0, 10.0, 0.0)], gravity=(0.0, 0.0, -980.0))
run(s, 30)
rotation, turned = s.results()
turned_dir = ue.rotate_vector(rotation[0][None], np.array([[0.0, 1.0, 0.0]]))[0]
actual_dir = (s.loc[1] - s.loc[0]) / np.linalg.norm(s.loc[1] - s.loc[0])
check("a bone with one child turns to point at it", turned[0] and np.allclose(turned_dir, actual_dir, atol=1e-9),
      (turned_dir, actual_dir))

finish()
