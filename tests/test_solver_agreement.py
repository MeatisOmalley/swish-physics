"""The C step and the numpy step agree to the bit on random scenes using every feature."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import check, finish, REPO

if REPO not in sys.path:                  # already there: CI's stand-in package goes before it
    sys.path.insert(0, REPO)
import numpy as np
from waifu_physics.solver import native, step_numpy, uemath as ue
from waifu_physics.solver.build import Skeleton, GroupSpec, build
from waifu_physics.solver.system import (Group, Shape, SPHERE_OUTER, SPHERE_INNER, CAPSULE, TAPERED, BOX, PLANE,
                                         COMPLIANCE_TYPES)

F32 = np.float32
# To the bit where both steps use one maths library (Windows: MSVC's, as Kawaii in Unreal). Elsewhere (Linux:
# glibc against numpy's own) sin, pow and acos may round their last bit differently, so CI sets a tolerance for
# where the two first part: at rounding level, the steps compute the same thing. After that, collisions and
# links amplify the difference as they would any rounding (chaos, measured up to 2 um in 170 frames).
TOLERANCE = float(os.environ.get("WAIFU_PHYSICS_AGREEMENT_TOLERANCE", "0"))
c_step = native.backend()
if c_step is step_numpy:
    check("the C step is available to compare against", sys.platform != "win32", native.reason())
    finish()


def random_quaternion(rng):
    q = rng.normal(size=4)
    return q / np.linalg.norm(q)


def scene(seed, fixed):
    """A skeleton of several chains, some branching, split into groups with links, curves and colliders."""
    rng = np.random.default_rng(seed)
    names, parents, pose, rotation = [], [], [], []
    roots = []
    for chain in range(int(rng.integers(3, 7))):
        root = len(names)
        names.append(f"root{chain}")
        parents.append(-1)
        pose.append(rng.normal(scale=20.0, size=3))
        rotation.append(random_quaternion(rng))
        roots.append(root)
        tip = root
        for depth in range(int(rng.integers(2, 7))):
            names.append(f"c{chain}_{depth}")
            parents.append(tip)
            pose.append(pose[tip] + rng.normal(scale=6.0, size=3) + (0.0, 0.0, -8.0))
            rotation.append(random_quaternion(rng))
            if depth == 1 and rng.random() < 0.4:              # a branch off the second bone
                names.append(f"c{chain}_branch")
                parents.append(len(names) - 2)
                pose.append(pose[-2] + rng.normal(scale=5.0, size=3))
                rotation.append(random_quaternion(rng))
            tip = names.index(f"c{chain}_{depth}")
    pose, rotation = np.array(pose), np.array(rotation)
    ref_length = [0.0 if p < 0 else float(np.linalg.norm(pose[i] - pose[p])) for i, p in enumerate(parents)]
    skeleton = Skeleton(names, parents, ref_length, pose, rotation)

    specs = []
    chains_per_group = np.array_split(roots, 2)
    for g, group_roots in enumerate(chains_per_group):
        curves = {name: (lambda r, a=rng.uniform(0.3, 1.5), b=rng.uniform(0.3, 1.5): a + (b - a) * r)
                  for name in ("damping", "stiffness", "radius", "limit_angle", "world_damping_location")
                  if rng.random() < 0.6}
        group = Group(
            settings=dict(damping=rng.uniform(0.02, 0.3), stiffness=rng.uniform(0.0, 0.2),
                          world_damping_location=rng.uniform(0.0, 1.0), world_damping_rotation=rng.uniform(0.0, 1.0),
                          radius=rng.uniform(1.0, 4.0), limit_angle=rng.choice([0.0, 25.0, 60.0])),
            curves=curves, dummy_bone_length=float(rng.choice([0.0, 4.0])),
            bone_subdivision_count=int(rng.integers(0, 3)), bone_subdivision_collision_only=bool(rng.random() < 0.7),
            bone_subdivision_densify_by_radius=bool(rng.random() < 0.3), planar_constraint=int(rng.integers(0, 4)),
            legacy_gravity=bool(g == 1 and rng.random() < 0.5),
            compliance_type=int(rng.integers(0, len(COMPLIANCE_TYPES))),
            iterations_before_collision=int(rng.integers(0, 3)), iterations_after_collision=int(rng.integers(1, 3)),
            constraint_subdivision_count=int(rng.integers(0, 3)),
            constraint_subdivision_feedback_scale=float(rng.uniform(0.5, 1.5)))
        chain_bones = [[i for i in range(len(names)) if names[i].startswith(f"c{names[r][4:]}_")] for r in group_roots]
        links = []
        for first, second in zip(chain_bones, chain_bones[1:] + chain_bones[:1]):
            for a, b in zip(first, second):
                if a != b and rng.random() < 0.7:
                    links.append((names[a], names[b], int(rng.choice([-1, 2, 5])), bool(rng.random() < 0.2)))
        exclude = [names[i] for i in range(len(names)) if names[i].endswith("_branch") and rng.random() < 0.3]
        specs.append(GroupSpec(group, roots=[(names[r], None) for r in group_roots], exclude=exclude, links=links))
    system = build(skeleton, specs, fixed_substepping=fixed)

    shapes = []
    for _g in specs:
        group_shapes = []
        for _ in range(int(rng.integers(2, 7))):
            kind = int(rng.choice([SPHERE_OUTER, SPHERE_INNER, CAPSULE, TAPERED, BOX, PLANE]))
            radius = rng.uniform(2.0, 8.0)
            group_shapes.append(Shape(
                kind, location=tuple(rng.normal(scale=15.0, size=3) + (0.0, 0.0, -25.0)),
                rotation=tuple(random_quaternion(rng)), radius=(40.0 if kind == SPHERE_INNER else radius),
                radius1=rng.uniform(1.0, 8.0), length=float(rng.choice([rng.uniform(5.0, 30.0), 1.0])),
                extent=tuple(rng.uniform(2.0, 10.0, size=3))))
        shapes.append(group_shapes)
    system.set_shapes(shapes)
    return system, skeleton


def frames(seed, count):
    """The same frame inputs for both runs: frame times, pose drift, component movement, teleports."""
    rng = np.random.default_rng(seed + 1000)
    out = []
    for f in range(count):
        dt = F32(1.0) / F32(rng.choice([24.0, 30.0, 60.0, 90.0, 144.0, 10.0]))
        drift = rng.normal(scale=0.4, size=3)
        turn = ue.quat_from_axis_angle(tuple(random_quaternion(rng)[:3] / np.linalg.norm(random_quaternion(rng)[:3])),
                                       F32(rng.normal(scale=0.03)))
        move = rng.normal(scale=2.0, size=3)
        move_rot = ue.quat_from_axis_angle((0.0, 0.0, 1.0), F32(rng.normal(scale=0.02)))
        teleport = rng.random() < 0.03
        out.append((dt, drift, turn, move, move_rot, teleport))
    return out


worst_frames = []
for seed in range(6):
    fixed = seed % 3 != 2
    systems = [scene(seed, fixed)[0] for _ in range(2)]
    base_pose = systems[0].frame_pose.copy()
    base_rot = systems[0].frame_pose_rot.copy()
    rows = systems[0].kind == 0
    first_mismatch = None
    worst = 0.0
    steps = 0
    for f, (dt, drift, turn, move, move_rot, teleport) in enumerate(frames(seed, 170)):
        for s in systems:
            s.frame_pose[rows] = base_pose[rows] + drift * np.sin(f * 0.1)
            s.frame_pose_rot[rows] = ue.quat_multiply(np.tile(turn, (rows.sum(), 1)), base_rot[rows])
            s.frame_move[:] = move
            s.frame_move_rot[:] = move_rot
            s.teleport[:] = teleport
            s.gravity[:] = (0.0, 0.0, -980.0)
        systems[0].step_frame(dt, step_numpy)
        systems[1].step_frame(dt, c_step)
        steps += 1
        same = all(np.array_equal(getattr(systems[0], name), getattr(systems[1], name))
                   for name in ("loc", "prev", "lambda_"))
        worst = max(worst, float(np.abs(systems[0].loc - systems[1].loc).max()))
        if not same and first_mismatch is None:
            first_mismatch = (f, float(np.abs(systems[0].loc - systems[1].loc).max()))
    kinds = np.bincount(systems[0].kind, minlength=4)
    check(f"scene {seed} ({'substeps' if fixed else 'legacy'}): {systems[0].n} points "
          f"({kinds[1]} tip, {kinds[2]} inter, {kinds[3]} bridge), {len(systems[0].link_a)} links, "
          f"{len(systems[0].shape_type)} shapes: C and numpy agree "
          + (f"(first parting within {TOLERANCE} cm)" if TOLERANCE else "to the bit") + f" for {steps} frames",
          first_mismatch is None or first_mismatch[1] <= TOLERANCE, (first_mismatch, worst))

finish()
