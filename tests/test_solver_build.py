"""The builder lays points out as Kawaii's node initialisation does.

A small skeleton, worked through AddModifyBone, CalcBoneLength and
InitBoneConstraints by hand:

    R (0,0,0) ── A (0,0,-10) ── A1 (0,0,-20)
      └──────── B (10,0,-10) ── B1 (10,0,-20)   B1 excluded

one subdivision per bone (collision only), 5 cm tip dummies, a link A1-B,
one bridge per link. Kawaii's order is:

    0 R   1 i(R,A)   2 A   3 i(A,A1)   4 A1   5 i(A1,tip)   6 tip(A1)
    7 i(R,B)   8 B   9 i(B,tip)   10 tip(B)   then bridges 11-13

B1's subdivision is inserted and rolled back when B1 turns out to be excluded.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import check, finish, REPO

sys.path.insert(0, REPO)
import numpy as np
from swish_physics.solver.build import Skeleton, GroupSpec, build, ComponentMotion
from swish_physics.solver.system import Group, KIND_BONE, KIND_TIP, KIND_INTER, KIND_BRIDGE

F32 = np.float32
names = ["R", "A", "A1", "B", "B1"]
pose = np.array([(0, 0, 0), (0, 0, -10), (0, 0, -20), (10, 0, -10), (10, 0, -20)], dtype=float)
skeleton = Skeleton(names=names, parents=[-1, 0, 1, 0, 3],
                    ref_length=[0.0, 10.0, 10.0, float(np.sqrt(200.0)), 10.0],
                    pose=pose, rotation=np.tile([0.0, 0.0, 0.0, 1.0], (5, 1)))
group = Group(dummy_bone_length=5.0, bone_subdivision_count=1, constraint_subdivision_count=1)
system = build(skeleton, [GroupSpec(group, roots=[("R", None)], exclude=["B1"], links=[("A1", "B", -1, False)])])

order = system.order                              # sorted position -> Kawaii index
kawaii = lambda values: system.unsorted(np.asarray(values))
kind = kawaii(system.kind)
parent_sorted = system.parent
parent = np.full(system.n, -1)
parent[order] = np.where(parent_sorted >= 0, order[np.maximum(parent_sorted, 0)], -1)
expected_kinds = [KIND_BONE, KIND_INTER, KIND_BONE, KIND_INTER, KIND_BONE, KIND_INTER, KIND_TIP,
                  KIND_INTER, KIND_BONE, KIND_INTER, KIND_TIP, KIND_BRIDGE, KIND_BRIDGE, KIND_BRIDGE]
check("points in Kawaii's order: bones, subdivisions, tip dummies, then bridges",
      list(kind) == expected_kinds, list(kind))
check("... with Kawaii's parents", list(parent) == [-1, 0, 1, 2, 3, 4, 5, 0, 7, 8, 9, -1, -1, -1], list(parent))
bone = kawaii(system.bone)
check("real points carry their bone; dummies none", list(bone) == [0, -1, 1, -1, 2, -1, -1, -1, 3, -1, -1, -1, -1, -1],
      list(bone))
check("B1's rolled-back subdivision leaves no point behind", system.n == 14, system.n)

location = kawaii(system.loc)
check("subdivisions sit halfway, tip dummies 5 cm along the bone's +Y",
      np.allclose(location[[1, 3, 5, 6, 9, 10]],
                  [(0, 0, -5), (0, 0, -15), (0, 2.5, -20), (0, 5, -20), (10, 2.5, -10), (10, 5, -10)]),
      location[[1, 3, 5, 6, 9, 10]])

# CalcBoneLength: a real bone's length is its whole rest offset, even after a subdivision,
# so lengths from the root double-count the subdivided part -- as in Kawaii.
b = F32(np.sqrt(200.0))
half_b = b / F32(2)
from_root = [F32(0), F32(5), F32(15), F32(20), F32(30), F32(32.5), F32(35),
             half_b, half_b + b, (half_b + b) + F32(2.5), ((half_b + b) + F32(2.5)) + F32(2.5)]
total = F32(35)
rate = kawaii(system.length_rate)
expected_rate = [F32(0)] + [F32(v / total) for v in from_root[1:]]
check("length rates from the root, in Kawaii's float arithmetic", np.array_equal(rate[:11], expected_rate),
      (rate[:11], expected_rate))

a, b_ = order[system.link_a], order[system.link_b]
pairs = sorted(zip(a.tolist(), b_.tolist()))
check("the link A1-B, plus automatic links along their subdivisions and tips",
      pairs == [(4, 8), (5, 9), (6, 10)], pairs)
check("links rest at their built distances", np.allclose(sorted(system.link_length), [np.sqrt(200.0)] * 3),
      system.link_length)
bridges = np.flatnonzero(kind == KIND_BRIDGE)
real_parent = kawaii(np.where(system.real_parent >= 0, order[np.maximum(system.real_parent, 0)], -1))
real_child = kawaii(np.where(system.real_child >= 0, order[np.maximum(system.real_child, 0)], -1))
check("one bridge dummy halfway along each link",
      sorted(zip(real_parent[bridges], real_child[bridges])) == [(4, 8), (5, 9), (6, 10)]
      and np.allclose(kawaii(system.alpha)[bridges], 0.5), list(zip(real_parent[bridges], real_child[bridges])))

# Component movement: a component moving 5 cm along X, seen from its new place, left the points 5 cm behind.
motion = ComponentMotion()
motion.update((0, 0, 0), (0, 0, 0, 1), (1, 1, 1))
move, turn, teleport = motion.update((5, 0, 0), (0, 0, 0, 1), (1, 1, 1))
check("component movement is the previous place seen from the new one", np.allclose(move, (-5, 0, 0)) and not teleport,
      move)
motion.consume(1.0, (5, 0, 0), (0, 0, 0, 1), (1, 1, 1))
_move, _turn, teleport = motion.update((500, 0, 0), (0, 0, 0, 1), (1, 1, 1))
check("a jump beyond 300 cm is a teleport", teleport)

finish()
