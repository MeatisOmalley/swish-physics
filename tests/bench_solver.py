"""Frame cost of the solver on a Peach-sized scene: one and five characters, numpy and C.

    blender -b --factory-startup --python tests/bench_solver.py

Not a test (the runner only picks up test_*.py): run it to compare against the
numbers in the plan. One character is 24 hair chains of 5 bones, 8 skirt chains
of 5 bones linked in a ring, 22 sphere and 6 capsule colliders; 24 fps, fixed
60 Hz substeps. Timings cover the whole frame loop: pose interpolation, the
step, and the substep bookkeeping -- everything but Blender I/O.
"""
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from swish_physics.solver import native, step_numpy, uemath as ue
from swish_physics.solver.build import Skeleton, GroupSpec, build
from swish_physics.solver.system import Group, Shape, SPHERE_OUTER, CAPSULE

F32 = np.float32


def character(offset, names, parents, pose, rotation, hair_roots, skirt_roots):
    for chain in range(32):
        skirt = chain >= 24
        angle = 2 * math.pi * (chain % 24 if not skirt else chain - 24) / (24 if not skirt else 8)
        base = np.array([math.cos(angle) * (12 if skirt else 8), math.sin(angle) * (12 if skirt else 8),
                         100.0 if skirt else 150.0]) + offset
        prev = -1
        for depth in range(5):
            names.append(f"{offset[0]}_{chain}_{depth}")
            parents.append(prev)
            pose.append(base + (0.0, 0.0, -8.0 * depth))
            rotation.append((0.0, 0.0, 0.0, 1.0))
            prev = len(names) - 1
            if depth == 0:
                (skirt_roots if skirt else hair_roots).append(names[-1])


def scene(characters):
    names, parents, pose, rotation = [], [], [], []
    specs = []
    for c in range(characters):
        hair, skirt = [], []
        character(np.array([c * 100.0, 0.0, 0.0]), names, parents, pose, rotation, hair, skirt)
        specs.append(GroupSpec(Group(), roots=[(r, None) for r in hair]))
        ring = [(f"{c * 100.0}_{24 + k}_{d}", f"{c * 100.0}_{24 + (k + 1) % 8}_{d}", -1, False)
                for k in range(8) for d in range(1, 5)]
        specs.append(GroupSpec(Group(), roots=[(r, None) for r in skirt], links=ring))
    pose = np.array(pose)
    ref = [0.0 if p < 0 else float(np.linalg.norm(pose[i] - pose[p])) for i, p in enumerate(parents)]
    system = build(Skeleton(names, parents, ref, pose, np.array(rotation)), specs)
    shapes = []
    for c in range(characters):
        body = [Shape(SPHERE_OUTER, location=(c * 100.0 + 5 * math.cos(k), 5 * math.sin(k), 90 + 3 * k), radius=6.0)
                for k in range(22)]
        body += [Shape(CAPSULE, location=(c * 100.0, 0.0, 60.0 + 10 * k), radius=5.0, length=20.0) for k in range(6)]
        shapes += [body, body]
    system.set_shapes(shapes)
    system.gravity[:] = (0.0, 0.0, -980.0)
    return system


for characters in (1, 5):
    for backend in (step_numpy, native.backend()):
        system = scene(characters)
        base = system.frame_pose.copy()
        times = []
        for f in range(120):
            system.frame_pose[:] = base + (math.sin(f * 0.2) * 5.0, 0.0, 0.0)
            system.frame_move[:] = (math.cos(f * 0.2), 0.0, 0.0)
            start = time.perf_counter()
            system.step_frame(F32(1.0) / F32(24.0), backend)
            times.append(time.perf_counter() - start)
        print(f"BENCH| {characters} character(s), {system.n} points, {len(system.link_a)} links, "
              f"{len(system.shape_type)} shapes, {backend.NAME}: {np.median(times[10:]) * 1000:.3f} ms a frame")
