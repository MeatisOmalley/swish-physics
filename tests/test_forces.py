"""External forces, wind and sync bones: Kawaii's own procedural-wind checks, each force's effect,
and the C and numpy steps agreeing to the bit with every force on."""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import check, finish, REPO

sys.path.insert(0, REPO)
import numpy as np
from waifu_physics.solver import forces as fx, native, step_numpy, uemath as ue
from waifu_physics.solver.build import Skeleton, GroupSpec, build
from waifu_physics.solver.curves import LinearCurve
from waifu_physics.solver.system import Group

F32 = np.float32

# --- Kawaii's ProceduralWind tests (KawaiiPhysicsProceduralWindTest.cpp), same inputs and expectations
check("ComputeStableHash matches Kawaii's five fixed values",
      [fx.stable_hash(0, 0, 0), fx.stable_hash(123, 0, 0), fx.stable_hash(123, 1, 0), fx.stable_hash(123, -1, 0),
       fx.stable_hash(-17, 42, 3)] == [672839204, 961409981, 688218621, 2476066305, 1090092324])
values = [fx.smooth_noise(F32(F32(-8.0) + F32(i) * F32(0.01)), 2468, 0) for i in range(2001)]
check("smooth noise stays in [-1, 1] (U from -8 to 12)", all(-1.0 - 1e-4 <= v <= 1.0 + 1e-4 for v in values))
check("... moves at most 0.1 per 0.01 of U", max(abs(a - b) for a, b in zip(values, values[1:])) <= 0.1)
check("... equals the grid value at grid points",
      all(fx.smooth_noise(F32(i), 2468, 1) == fx.noise_at(i, 2468, 1) for i in range(-8, 9)))
check("... and differs between channels", any(fx.smooth_noise(F32(u) * F32(0.3), 5, 1) != fx.smooth_noise(
    F32(u) * F32(0.3), 5, 2) for u in range(50)))


def wind(**params):
    return fx.ProceduralWindForce(fx.ForceSpec(fx.PROCEDURAL_WIND, params=params))


near = lambda a, b, tol=1.0e-4: abs(float(a) - float(b)) <= tol
w = wind(constant=7.25)
check("constant only: the total is the constant at any time",
      all(near(w.sample(t, 0.5)[6], 7.25) and w.sample(t, 0.5)[0] == F32(7.25) for t in (0.0, 0.1, 0.5, 1.0, 3.75)))
sway = wind(sway=1.0, sway_period=2.0)
check("sway peaks at a quarter period", near(sway.sample(0.5)[1], 1.0))
check("sway is periodic", near(sway.sample(0.37)[1], sway.sample(2.37)[1], 1e-6))
shifted = wind(sway=1.0, sway_period=2.0, sway_phase=90.0)
check("a 90-degree sway phase peaks at t=0 and equals a quarter-period shift",
      near(shifted.sample(0.0)[1], 1.0) and near(shifted.sample(0.37)[1], sway.sample(0.87)[1], 1e-6))
ripple = wind(ripple=1.0, ripple_period=2.0, ripple_phase=90.0)
check("ripple phase", near(ripple.sample(0.0, 0.0)[2], 1.0))
check("ripple is periodic", near(ripple.sample(0.37, 0.35)[2], ripple.sample(2.37, 0.35)[2], 1e-6))
wave = wind(ripple=1.0, ripple_period=2.0, ripple_delay=180.0)
check("root and tip ripple are in opposite phase", wave.sample(0.5, 0.0)[2] * wave.sample(0.5, 1.0)[2] < 0)
peak = lambda t: int(np.argmax([wave.sample(t, k / 1000.0)[2] for k in range(1001)]))
check("the ripple's peak travels root to tip", peak(1.0) > peak(0.5), (peak(0.5), peak(1.0)))
cycle = wind(cycle_min=0.2, cycle_max=1.5, cycle_period=1.37, cycle_phase=17.0)
check("the strength cycle stays within its range",
      all(0.2 - 1e-4 <= cycle.sample(F32(i) * F32(0.013))[3] <= 1.5 + 1e-4 for i in range(1000)))
flat = wind(cycle_min=0.625, cycle_max=0.625)
check("an equal range makes the cycle constant", all(near(flat.sample(t)[3], 0.625) for t in (0.0, 0.4, 1.7, 8.0)))
reverse = wind(cycle_min=1.5, cycle_max=0.25, cycle_period=2.0, cycle_phase=90.0)
check("a reversed range starts at its max endpoint", near(reverse.sample(0.0)[3], 0.25))
a, b = wind(random=1.0, seed=3), wind(random=1.0, seed=3)
check("the same seed gives the same samples", all(a.sample(t * 0.1) == b.sample(t * 0.1) for t in range(64)))
check("another seed changes the random series",
      any(a.sample(t * 0.1)[4] != wind(random=1.0, seed=4).sample(t * 0.1)[4] for t in range(64)))
stream = fx.RandomStream(12345)
draws = [stream.frand() for _ in range(1000)]
check("FRandomStream draws lie in [0, 1)", all(0.0 <= d < 1.0 for d in draws) and len(set(draws)) > 990)


# --- a hanging chain to push around
def chain(forces=(), sync=(), bones=4, **group_args):
    names = ["root"] + [f"b{i}" for i in range(bones)]
    parents = [-1] + list(range(bones))
    pose = np.array([(0.0, 0.0, -10.0 * i) for i in range(bones + 1)])
    rotation = np.tile(ue.quat_from_axis_angle((1.0, 0.0, 0.0), math.pi), (bones + 1, 1))
    ref = [0.0] + [10.0] * bones
    group = Group(settings=dict(damping=0.1, stiffness=0.05, world_damping_location=0.8, world_damping_rotation=0.8,
                                radius=1.0, limit_angle=0.0), dummy_bone_length=5.0, forces=list(forces),
                  sync_bones=list(sync), **group_args)
    system = build(Skeleton(names, parents, ref, pose, rotation), [GroupSpec(group, roots=[("root", None)])])
    system.bone_names = [names[b] if b >= 0 else "" for b in system.bone]
    return system


def run(system, frames=60, backend=step_numpy, wind=None):
    base_pose, base_rot = system.frame_pose.copy(), system.frame_pose_rot.copy()
    real = system.kind == 0
    for f in range(frames):
        system.frame_number = f
        system.frame_pose[real] = base_pose[real]
        system.frame_pose_rot[real] = base_rot[real]
        system.wind = [wind] * len(system.groups)
        system.step_frame(F32(1.0 / 30.0), backend)
    return system


def tip(system):
    return system.loc[int(np.flatnonzero(system.kind == 1)[0])]


rest = tip(run(chain()))
basic = run(chain([fx.ForceSpec(fx.BASIC, space=fx.COMPONENT, params=dict(direction=(50.0, 0.0, 0.0)))]))
check("a Basic force pushes the chain its way", tip(basic)[0] - rest[0] > 5.0, tip(basic) - rest)
pulse = run(chain([fx.ForceSpec(fx.BASIC, space=fx.COMPONENT, params=dict(direction=(50.0, 0.0, 0.0), interval=1.0))]))
check("... less when it only fires once an interval", 0.0 < tip(pulse)[0] - rest[0] < tip(basic)[0] - rest[0],
      (tip(pulse)[0], tip(basic)[0]))
only = run(chain([fx.ForceSpec(fx.BASIC, space=fx.COMPONENT, apply_bones=frozenset({"b0"}),
                               params=dict(direction=(50.0, 0.0, 0.0)))]))
ignored = run(chain([fx.ForceSpec(fx.BASIC, space=fx.COMPONENT, ignore_bones=frozenset({"b0", "b1", "b2", "b3"}),
                                  params=dict(direction=(50.0, 0.0, 0.0)))]))
check("an apply filter limits it to the named bones", 0.0 < tip(only)[0] - rest[0] < tip(basic)[0] - rest[0])
check("an ignore filter leaves only the unnamed (the tip dummy)", 0.0 < tip(ignored)[0] - rest[0] < tip(only)[0] - rest[0],
      (tip(ignored)[0], tip(only)[0]))
rated = run(chain([fx.ForceSpec(fx.BASIC, space=fx.COMPONENT, rate_curve=LinearCurve([0.0, 1.0], [0.0, 0.0]),
                                params=dict(direction=(50.0, 0.0, 0.0)))]))
check("a zero rate curve cancels it", np.allclose(tip(rated), rest, atol=1e-9))
gravity = run(chain([fx.ForceSpec(fx.GRAVITY, random_range=(980.0, 980.0),
                                  params=dict(override_direction=True, direction=(1.0, 0.0, 0.0)))]))
check("a Gravity force accelerates the chain along its direction", tip(gravity)[0] - rest[0] > 5.0)
slots = run(chain([fx.ForceSpec(fx.GRAVITY), fx.ForceSpec(fx.BASIC)]), frames=1)
check("... through velocity (ApplyToVelocity), where Basic adds to position (Apply)",
      slots.vforce.shape[0] == 1 and slots.pforce.shape[0] == 1 and slots.vmask[0].all())
blown = run(chain([fx.ForceSpec(fx.WIND)]), wind=((0.0, 1.0, 0.0), 40.0))
check("a Wind force follows the scene's wind", tip(blown)[1] - rest[1] > 5.0, tip(blown) - rest)
still = run(chain([fx.ForceSpec(fx.WIND)]), wind=None)
check("... and does nothing without wind", np.allclose(tip(still), rest, atol=1e-9))
scene_wind = run(chain(enable_wind=True, wind_scale=1.0), wind=((0.0, 1.0, 0.0), 1.0))
check("the group's own scene wind blows too", tip(scene_wind)[1] - rest[1] > 1.0, tip(scene_wind) - rest)
simple = run(chain(simple_external_force=(0.0, 30.0, 0.0), world_space_simple_external_force=False))
check("the simple external force pushes", tip(simple)[1] - rest[1] > 5.0)
curve = LinearCurve([0.0, 0.5, 1.0], [0.0, 60.0, 0.0])
curved = run(chain([fx.ForceSpec(fx.CURVE, space=fx.COMPONENT,
                                 params=dict(curves=(curve, None, None), max_time=1.0, evaluate=fx.AVERAGE))]))
check("a Curve force follows its curve over time", tip(curved)[0] - rest[0] > 1.0)
procedural = run(chain([fx.ForceSpec(fx.PROCEDURAL_WIND, space=fx.COMPONENT,
                                     params=dict(direction=(0.0, 1.0, 0.0), constant=40.0))]))
check("procedural wind blows along its direction", tip(procedural)[1] - rest[1] > 5.0)
bone_space = run(chain([fx.ForceSpec(fx.BASIC, space=fx.BONE, params=dict(direction=(0.0, 50.0, 0.0)))]))
check("a bone-space force turns with the bones (turned 180 degrees about X, their +Y is world -Y)",
      tip(bone_space)[1] < rest[1] - 5.0 and abs(tip(bone_space)[0] - rest[0]) < 1e-9, tip(bone_space) - rest)

# --- sync bones: a leg moving forward carries the skirt's pose with it
def synced(location, attenuation=False, include_children=True):
    target = fx.SyncTargetSpec(root=-1, include_children=include_children)
    spec = fx.SyncSpec(location=np.array(location, dtype=float), rest_location=np.zeros(3), targets=[target],
                       attenuation=attenuation, inner_radius=1.0, outer_radius=2.0, max_attenuation=1.0)
    s = chain(sync=[spec])
    target.root = int(np.flatnonzero((s.parent >= 0) & (s.kind == 0))[0])
    return s


s = synced((0.0, 8.0, 0.0))
before = s.frame_pose.copy()
s.prepare_frame()
moved = np.flatnonzero(np.abs(s.frame_pose - before).max(axis=1) > 1e-9)
real = [i for i in moved if s.kind[i] == 0]
check("a sync bone moves its target bones' pose", len(real) == 4, len(real))
lengths_kept = all(abs(np.linalg.norm(s.frame_pose[i] - s.frame_pose[s.parent[i]]) - s.bone_length[i]) < 1e-4
                   for i in real)
check("... keeping each bone's length to its parent", lengths_kept)
check("... toward the source's movement", s.frame_pose[real[-1]][1] > before[real[-1]][1] + 1.0)
far = synced((0.0, 8.0, 0.0), attenuation=True)
before = far.frame_pose.copy()
far.prepare_frame()
check("attenuation beyond the outer radius removes it", np.allclose(far.frame_pose, before))
alone = synced((0.0, 8.0, 0.0), include_children=False)
before = alone.frame_pose.copy()
alone.prepare_frame()
check("without children only the target root moves",
      int((np.abs(alone.frame_pose - before).max(axis=1) > 1e-9).sum()) == 1)

# --- C and numpy agree with everything on
c_step = native.backend()
if c_step is step_numpy:
    check("the C step is available to compare against", sys.platform != "win32", native.reason())
else:
    def everything():
        rate = LinearCurve([0.0, 1.0], [0.5, 1.5])
        return chain([
            fx.ForceSpec(fx.GRAVITY, random_range=(400.0, 900.0), rate_curve=rate),
            fx.ForceSpec(fx.BASIC, space=fx.WORLD, random_range=(0.5, 2.0), params=dict(direction=(20.0, 5.0, 0.0),
                                                                                         interval=0.2)),
            fx.ForceSpec(fx.WIND, params=dict(noise_angle=20.0), ignore_bones=frozenset({"b1"})),
            fx.ForceSpec(fx.PROCEDURAL_WIND, space=fx.BONE, rate_curve=rate,
                         params=dict(direction=(0.0, 1.0, 0.3), constant=10.0, sway=8.0, ripple=6.0, random=4.0,
                                     noise_angle=15.0, seed=9, cycle_min=0.5, cycle_max=1.2)),
            fx.ForceSpec(fx.CURVE, space=fx.COMPONENT, params=dict(curves=(curve, curve, None), max_time=1.0,
                                                                   evaluate=fx.MAX, substeps=4)),
        ], sync=[], enable_wind=True, wind_direction_noise_angle=10.0, simple_external_force=(3.0, 0.0, 1.0))

    a, b = everything(), everything()
    run(a, 120, step_numpy, wind=((0.3, 1.0, 0.0), 30.0))
    run(b, 120, c_step, wind=((0.3, 1.0, 0.0), 30.0))
    check("with every force, wind and the simple force on, C and numpy agree to the bit for 120 frames",
          np.array_equal(a.loc, b.loc) and np.array_equal(a.prev, b.prev), float(np.abs(a.loc - b.loc).max()))
    check("... and the forces moved the chain", float(np.abs(tip(a) - rest).max()) > 1.0)

finish()
