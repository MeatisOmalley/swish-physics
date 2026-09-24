"""Kawaii Physics' golden scenarios (KawaiiPhysicsGoldenTest.cpp at 64cbc77), rebuilt on our System.

The golden values are Kawaii's bone positions after 200 frames of 1/90 s, as
the bit patterns of doubles. The scenarios drive Kawaii's step directly, the
way its test harness (KawaiiPhysicsTestHarness.h) does: hand-built vertical
chains, settings set on every bone, a fixed component movement every frame.
"""
import struct

import numpy as np

from swish_physics.solver import uemath as ue
from swish_physics.solver.system import System, Group, Shape, SPHERE_OUTER, CAPSULE, BOX, PLANE

F32 = np.float32

GOLDEN = {
    "ChainLegacy": [
        0x0000000000000000, 0x0000000000000000, 0x0000000000000000,
        0x400cb36886109a0b, 0x3fd84b5227aa6490, 0xc022a734f8496675,
        0x401c64444ee9b3e4, 0x3ff206c7630a3310, 0xc032a90d9826a689,
        0x4024fc0cb93b114e, 0x4001de346c91e3c5, 0xc03c005574c5ce68,
        0x402b77cb6a41f1f5, 0x400d7a05654f962e, 0xc042acb73b60a59a,
        0x4030c9ccbe978f78, 0x4015d78e9841be9c, 0xc0475a2a1cb82e99,
        0x40339f946dc5e8b9, 0x401e278f44e15106, 0xc04c087a4e75b4de,
        0x4036364550098a76, 0x4023c85dcc758517, 0xc0505bce39f8f42c,
        0x403886c62e05fb9a, 0x4028f5512c45a1e0, 0xc052b3ff19ce6d8b,
        0x403a81ee9349fb99, 0x402e8624839a69e8, 0xc0550d7f6824d7a6,
        0x403c0f00aca3e782, 0x40324b1bc7f24e16, 0xc05767401986a3e6,
        0x403d47e342cf0b37, 0x4035cde3eb6a23ea, 0xc059b96202d95a7b,
    ],
    "Chain": [
        0x0000000000000000, 0x0000000000000000, 0x0000000000000000,
        0x3ff8ffa4a44df976, 0x3fb234162e8d0238, 0xc023c0fd63fc7242,
        0x4008e93bb10ca415, 0x3fcb1304e188803d, 0xc033c13dcc056a30,
        0x40129f3cb83d2979, 0x3fdb02009d4ad6c0, 0xc03da21064d86560,
        0x4018bc9eb0e7e69f, 0x3fe678073152a192, 0xc043c17aa10327ac,
        0x401eca1546eea61a, 0x3ff0d34dc7b5d9ee, 0xc048b1f32620072e,
        0x4022637b3b82fb6d, 0x3ff77e422a9ed826, 0xc04da266ffa6f07d,
        0x4025572f2f7697f0, 0x3fff135ba017d69c, 0xc0514976e748807c,
        0x40282c71cfd5026e, 0x4003b0c25a06cbd2, 0xc053c223af1fd841,
        0x402abdef7182e280, 0x4008af783fe5a74c, 0xc0563b94cec6090f,
        0x402d41f062791b25, 0x401028b5e8ac2a19, 0xc058b38e2bcd6587,
        0x40307cfc66c717e4, 0x4015cde3469d31a8, 0xc05b21df93351f51,
    ],
    "Constraint": [
        0x3ff7ffffeb3927ee, 0x0000000000000000, 0x0000000000000000,
        0x3ff800001d987181, 0x3ff0d12a2c15900f, 0xc023e3a3e0420728,
        0x3ff8000030c14c73, 0x4000caf5099541a6, 0xc033e3b8d71aab28,
        0x3ff800001ad02486, 0x40092d24ce1de03a, 0xc03dd5a0608f99f6,
        0x3ff7ffffe2fde461, 0x4010c7a894a70a50, 0xc043e3c3fac2b720,
        0x3ff7ffffca11cc6f, 0x4014f8ffda9c5f45, 0xc048dcb6e9b74ca2,
        0x3ff800000d185d1b, 0x40192b642843de93, 0xc04dd5a64cd88919,
        0x3ff7ffffd487e69a, 0x401d5c8d6607c9f8, 0xc051674ceb858fa5,
        0x402b00000298db03, 0x0000000000000000, 0x0000000000000000,
        0x402afffffc4cf1d2, 0x3ff0d12a2c15900f, 0xc023e3a3e0420728,
        0x402afffff9e7d66f, 0x4000caf5099541a6, 0xc033e3b8d71aab28,
        0x402afffffca5fb65, 0x40092d24ce1de03a, 0xc03dd5a0608f99f6,
        0x402b000003a04369, 0x4010c7a894a70a50, 0xc043e3c3fac2b720,
        0x402b000006bdc671, 0x4014f8ffda9c5f45, 0xc048dcb6e9b74ca2,
        0x402afffffe5cf467, 0x40192b642843de93, 0xc04dd5a64cd88919,
        0x402b0000056f0342, 0x401d5c8d6607c9f8, 0xc051674ceb858fa5,
    ],
    "Collision": [
        0x0000000000000000, 0x0000000000000000, 0x0000000000000000,
        0x0000000000000000, 0x0000000000000000, 0xc024000000000000,
        0x0000000000000000, 0x0000000000000000, 0xc034000000000000,
        0x0000000000000000, 0xc0131eb4225deaee, 0xc03cc89a7895568a,
        0xbff863480c9f1c6b, 0xc01f9c1da7f35dc7, 0xc043148c8dd8be77,
        0xc001ef2fec265986, 0xc02304b5789e6cc4, 0xc04800935d0b9b3d,
        0xc0019a2e27b8b835, 0xc022d50e8fad31ce, 0xc04d0082592cd67a,
        0xc00198673ec1e897, 0xc025e99a3b32febe, 0xc050f89e5fc53ab0,
        0xc00197cc42c880be, 0xc025ff67b9a80bda, 0xc053789ce376bcb4,
        0xc0019962ef72e999, 0xc025fb85ef0e2783, 0xc055f89cd7481e49,
        0x40002f84af5f1449, 0xc02b56100e212587, 0xc05822de70c35520,
        0x3ffcd8cf920b97ca, 0xc02b0eb42ae5698e, 0xc05aa2a6c585900b,
    ],
}


def bits(value):
    return struct.unpack("<Q", struct.pack("<d", float(value)))[0]


def settings(radius, limit_angle):
    """MakeSettings in KawaiiPhysicsGoldenTest.cpp."""
    return dict(damping=0.15, world_damping_location=0.2, world_damping_rotation=0.3, stiffness=0.07,
                radius=radius, limit_angle=limit_angle)


def chain_points(chains, count, spacing, lateral=0.0):
    """BuildVerticalChain / BuildTwoVerticalChains: straight down, 'spacing' apart."""
    points = {key: [] for key in ("parent", "group", "kind", "real_parent", "real_child", "alpha",
                                  "location", "pose", "pose_rotation", "length_rate")}
    for chain in range(chains):
        base = chain * count
        for i in range(count):
            # Origin + offset, as the harness builds them (so the root is +0.0, not -0.0).
            location = (0.0 + lateral * chain, 0.0 + 0.0, 0.0 + -(spacing * i))
            points["parent"].append(base + i - 1 if i > 0 else -1)
            points["group"].append(0)
            points["kind"].append(0)
            points["real_parent"].append(-1)
            points["real_child"].append(-1)
            points["alpha"].append(0.0)
            points["location"].append(location)
            points["pose"].append(location)
            points["pose_rotation"].append((0.0, 0.0, 0.0, 1.0))
            points["length_rate"].append(0.0)
    return points


def run(name, backend):
    """Returns (positions in Kawaii's order, golden bit patterns)."""
    fixed = name != "ChainLegacy"
    if name in ("Chain", "ChainLegacy"):
        group = Group(settings=settings(2.0, 0.0))
        system = System([group], chain_points(1, 12, 10.0), {}, fixed_substepping=fixed)
        system.frame_move[0] = (F32(0.3), 0.0, 0.0)          # FVector(0.3f, 0.0f, 0.0f)
        system.frame_move_rot[0] = ue.quat_from_axis_angle((0.0, 0.0, 1.0), F32(0.01))
    elif name == "Constraint":
        group = Group(settings=settings(2.0, 0.0), iterations_before_collision=2, iterations_after_collision=2)
        links = dict(a=list(range(8)), b=[8 + d for d in range(8)], length=[12.0] * 8)
        system = System([group], chain_points(2, 8, 10.0, lateral=15.0), links)
        system.frame_move[0] = (0.0, F32(0.2), 0.0)          # FVector(0.0f, 0.2f, 0.0f)
    else:
        group = Group(settings=settings(3.0, 30.0))
        system = System([group], chain_points(1, 12, 10.0), {})
        system.set_shapes([[
            Shape(SPHERE_OUTER, location=(2.0, 0.0, -45.0), radius=8.0),
            Shape(CAPSULE, location=(0.0, 2.0, -55.0), radius=4.0, length=50.0),
            Shape(BOX, location=(0.0, -2.0, -75.0), extent=(6.0, 6.0, 8.0)),
            Shape(PLANE, location=(0.0, 0.0, -95.0)),
        ]])
    system.gravity[0] = (0.0, 0.0, -980.0)
    system.resolve_settings()
    frame_dt = F32(1.0) / F32(90.0)
    for _ in range(200):
        system.step_frame(frame_dt, backend)
    return system.unsorted(system.loc), GOLDEN[name]


def compare(positions, golden):
    """(values matching bit for bit, largest difference in cm)."""
    flat = positions.ravel()
    exact = sum(bits(v) == g for v, g in zip(flat, golden))
    expected = np.array([struct.unpack("<d", struct.pack("<Q", g))[0] for g in golden])
    return exact, float(np.abs(flat - expected).max())
