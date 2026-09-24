"""Live simulation: every Swish group in the scene, stepped as frames change.

One System holds every group of every armature. After Blender evaluates a
frame's animation (frame_change_post), the runtime reads each armature's
input pose, steps the solver by the frames' time, and writes the chains'
rotations back. It starts over -- points at the pose -- on the scene's first
frame, on a jump of more than a few frames or backwards, and whenever a
group's structure changes.
"""
import math

import bpy
import numpy as np
from bpy.app.handlers import persistent

from ..data import colliders as collider_objects
from ..data import curves as group_curves
from ..data.props import FORCE_CHANNELS
from ..solver import forces as frame_forces
from ..solver import native
from ..solver.build import Skeleton, GroupSpec, build, ComponentMotion
from ..solver.curves import LinearCurve
from ..solver.system import Group, COMPLIANCE_TYPES, PLANAR_NONE, PLANAR_X, PLANAR_Y, PLANAR_Z
from . import cache as frame_cache
from . import io

F32 = np.float32
PLANAR = {"NONE": PLANAR_NONE, "X": PLANAR_X, "Y": PLANAR_Y, "Z": PLANAR_Z}
MAX_FRAME_STEP = 4            # frames forward that still count as playing on; more is a jump

_runtimes = {}                # scene pointer -> Runtime
_dirty = set()                # scene pointers whose groups changed shape
_writing = False              # our own writes are in progress (the cache ignores them)


def invalidate(scene=None):
    """Something that changes the result changed: drop cached frames."""
    for key, current in _runtimes.items():
        if scene is None or key == scene.as_pointer():
            current.cache.clear()


def mark_dirty(scene=None):
    """A group's structure changed: rebuild before the next step."""
    if scene is None:
        _dirty.update(_runtimes.keys())
        _dirty.add("all")
    else:
        _dirty.add(scene.as_pointer())


def armatures(scene):
    """Armature objects in the scene with at least one enabled group."""
    return [obj for obj in scene.objects if obj.type == "ARMATURE"
            and any(group.enabled and len(group.roots) for group in obj.swish.groups)]


class Runtime:
    """The simulation of one scene."""

    def __init__(self, scene):
        self.scene_pointer = scene.as_pointer()
        self.cm = io.cm_per_unit(scene)
        self.rigs = [io.Rig(obj) for obj in armatures(scene)]
        self.group_props = []          # (rig index, group index in obj.swish.groups)
        names, parents, ref_length, pose, rotation = [], [], [], [], []
        offsets = []
        for r, rig in enumerate(self.rigs):
            offsets.append(len(names))
            # The chain first, so the pose read rebuilds it from keyed channels, not last frame's physics.
            groups = [props for props in rig.obj.swish.groups if props.enabled and len(props.roots)]
            rig.set_chain(rig.subtree([root.name for props in groups for root in props.roots],
                                      [bone.name for props in groups for bone in props.excluded]))
            input_pose = rig.read()
            names += [f"{r}|{name}" for name in rig.names]
            parents += [p + offsets[-1] if p >= 0 else -1 for p in rig.parents]
            ref_length += list(rig.ref_lengths() * self.cm)
            pose.append(input_pose[:, :3, 3] * self.cm)
            rotation.append(io.quats_from_matrices(io.unscaled(input_pose[:, :3, :3])))
        self.offsets = offsets
        specs = []
        for r, rig in enumerate(self.rigs):
            for g, props in enumerate(rig.obj.swish.groups):
                if not props.enabled or not len(props.roots):
                    continue
                self.group_props.append((r, g))
                specs.append(self._spec(r, props))
        scene_settings = scene.swish
        self.system = build(Skeleton(names, parents, ref_length,
                                     np.concatenate(pose) if pose else np.zeros((0, 3)),
                                     np.concatenate(rotation) if rotation else np.zeros((0, 4))),
                            specs, target_framerate=scene_settings.target_framerate,
                            max_substeps=scene_settings.max_substeps,
                            fixed_substepping=scene_settings.fixed_substepping)
        s = self.system
        self.real = np.flatnonzero(s.bone >= 0)
        combined = s.bone[self.real]
        self.rig_of_point = np.searchsorted(np.array(offsets + [len(names)]), combined, side="right") - 1
        self.bone_of_point = combined - np.array(offsets)[self.rig_of_point] if len(offsets) else combined
        for r, rig in enumerate(self.rigs):
            rig.set_chain(self.bone_of_point[self.rig_of_point == r])
        # Force filters and sync targets name bones; the points of each group by bone name.
        for i in self.real:
            s.bone_names[i] = names[s.bone[i]].split("|", 1)[1]
        self.point_of = {(int(s.group[i]), s.bone_names[i]): int(i) for i in self.real}
        self.motions = [ComponentMotion() for _ in self.group_props]
        self.last_frame = None
        self.backend = native.backend()
        self.cache = {}                   # frame -> cache.Snapshot
        self.cache_key = frame_cache.key(scene)
        self.own_update = False           # the next depsgraph update is our own write
        self.collider_prints = {}

    def _spec(self, r, props):
        prefix = f"{r}|"
        cm = self.cm
        group = Group(
            settings=self._settings(props), curves=group_curves.curves(props),
            dummy_bone_length=props.dummy_bone_length * cm,
            bone_subdivision_count=props.bone_subdivision_count,
            bone_subdivision_collision_only=props.bone_subdivision_collision_only,
            bone_subdivision_densify_by_radius=props.bone_subdivision_densify_by_radius,
            planar_constraint=PLANAR[props.planar_constraint], legacy_gravity=props.legacy_gravity,
            compliance_type=COMPLIANCE_TYPES.index(props.compliance),
            iterations_before_collision=props.iterations_before_collision,
            iterations_after_collision=props.iterations_after_collision,
            auto_add_child_dummy_constraint=props.auto_child_dummy_links,
            constraint_subdivision_count=props.bridge_count,
            constraint_subdivision_feedback_scale=props.bridge_feedback)
        links = [(prefix + link.bone_a, prefix + link.bone_b,
                  -1 if link.compliance == "GROUP" else COMPLIANCE_TYPES.index(link.compliance),
                  link.exclude_from_subdivision) for link in props.links]
        return GroupSpec(group, roots=[(prefix + root.name, None) for root in props.roots],
                         exclude=[prefix + bone.name for bone in props.excluded], links=links)

    def _settings(self, props):
        """Kawaii's units: radius in centimetres, limit angle in degrees."""
        return dict(damping=props.damping, stiffness=props.stiffness,
                    world_damping_location=props.world_damping_location,
                    world_damping_rotation=props.world_damping_rotation,
                    radius=props.radius * self.cm, limit_angle=math.degrees(props.limit_angle))

    def _frame_forces(self, g, rig, props):
        """This frame's external forces, wind settings and sync bones of a group, in Kawaii's units."""
        grp, cm = self.system.groups[g], self.cm
        grp.forces = [self._force(f) for f in props.forces]
        grp.sync_bones = [self._sync(g, rig, sync) for sync in props.sync_bones]
        grp.simple_external_force = tuple(np.array(props.simple_external_force) * cm)
        grp.world_space_simple_external_force = props.world_space_simple_external_force
        grp.enable_wind = props.enable_wind
        grp.wind_scale = props.wind_scale
        grp.wind_direction_noise_angle = math.degrees(props.wind_direction_noise_angle)

    def _force(self, f):
        cm, kind = self.cm, f.kind
        scale = (f.random_min, f.random_max)
        if kind == "BASIC":
            params = dict(direction=tuple(np.array(f.direction) * cm), interval=f.interval)
        elif kind == "GRAVITY":
            params = dict(override_direction=f.override_direction, direction=tuple(f.direction))
            scale = (f.random_min * cm, f.random_max * cm)          # the pull's strength is an acceleration
        elif kind == "CURVE":
            channels = []
            for axis, channel in enumerate(FORCE_CHANNELS):
                found = group_curves.curve(f, channel, required=True)
                channels.append(None if found is None else LinearCurve(
                    found.times * F32(f.duration), found.values * F32(f.amplitude[axis] * cm)))
            params = dict(curves=tuple(channels), max_time=f.duration, time_scale=f.time_scale,
                          evaluate=f.evaluate, substeps=f.substeps)
        elif kind == "WIND":
            params = dict(noise_angle=math.degrees(f.noise_angle))
        else:
            params = dict(direction=tuple(f.direction), noise_angle=math.degrees(f.noise_angle),
                          noise_period=f.noise_period, time_scale=f.time_scale, constant=f.constant * cm,
                          sway=f.sway * cm, sway_period=f.sway_period, sway_phase=math.degrees(f.sway_phase),
                          ripple=f.ripple * cm, ripple_period=f.ripple_period,
                          ripple_phase=math.degrees(f.ripple_phase), ripple_delay=math.degrees(f.ripple_delay),
                          cycle_min=f.cycle_min, cycle_max=f.cycle_max, cycle_period=f.cycle_period,
                          cycle_phase=math.degrees(f.cycle_phase), random=f.random * cm,
                          random_period=f.random_period, seed=f.seed)
        return frame_forces.ForceSpec(
            kind, enabled=f.enabled, space="WORLD" if kind in ("GRAVITY", "WIND") else f.space,
            apply_bones=frozenset(b.name for b in f.apply_bones),
            ignore_bones=frozenset(b.name for b in f.ignore_bones),
            random_range=scale, rate_curve=group_curves.curve(f, "rate"), params=params)

    def _sync(self, g, rig, sync):
        cm = self.cm
        index = rig.index.get(sync.bone, -1)
        location = rest = None
        if index >= 0:
            location = rig.pose[index][:3, 3] * cm
            rest = rig.rest[index][:3, 3] * cm
        distance = group_curves.curve(sync, "distance")
        if distance is not None:
            distance = LinearCurve(distance.times * F32(sync.distance * cm), distance.values)
        targets = [frame_forces.SyncTargetSpec(root=self.point_of.get((g, t.bone), -1),
                                               include_children=t.include_children,
                                               rate_curve=group_curves.curve(t, "rate")) for t in sync.targets]
        return frame_forces.SyncSpec(
            location=location, rest_location=rest, targets=targets, global_scale=tuple(sync.global_scale),
            distance_curve=distance, directions=(sync.direction_x, sync.direction_y, sync.direction_z),
            attenuation=sync.attenuation, inner_radius=sync.inner_radius * cm, outer_radius=sync.outer_radius * cm,
            max_attenuation=sync.max_attenuation)

    def _group(self, g):
        r, index = self.group_props[g]
        return self.rigs[r], self.rigs[r].obj.swish.groups[index]

    # ------------------------------------------------------------------ frames
    def _read(self, scene):
        """This frame's input pose and component movement into the system."""
        s = self.system
        poses = [rig.read() for rig in self.rigs]
        pose = np.zeros((len(self.real), 4, 4))
        for r, rig_pose in enumerate(poses):
            rows = self.rig_of_point == r
            pose[rows] = rig_pose[self.bone_of_point[rows]]
        s.frame_pose[self.real] = pose[:, :3, 3] * self.cm
        s.frame_pose_rot[self.real] = io.quats_from_matrices(io.unscaled(pose[:, :3, :3]))
        s.frame_pose_scale[self.real] = np.linalg.norm(pose[:, :3, :3], axis=1)
        s.frame_number = scene.frame_current
        wind = scene_wind(scene, self.cm)
        for g in range(len(self.group_props)):
            rig, props = self._group(g)
            s.groups[g].settings = self._settings(props)
            s.groups[g].curves = group_curves.curves(props)
            self._frame_forces(g, rig, props)
            s.wind[g] = wind
            world = rig.obj.matrix_world
            s.world_to_sim[g] = np.linalg.inv(np.array(world.to_3x3()))
            gravity = np.array(props.gravity, dtype=float)
            if props.use_scene_gravity:
                gravity = gravity * abs(scene.gravity[2])
            if props.use_world_space_gravity:
                gravity = np.linalg.inv(np.array(world.to_3x3())) @ gravity
            s.gravity[g] = gravity * self.cm
            location, rotation, scale = world.decompose()
            motion = self.motions[g]
            motion.teleport_distance = F32(props.teleport_distance * self.cm)
            motion.teleport_rotation = F32(math.degrees(props.teleport_rotation))
            move, move_rot, teleport = motion.update(np.array(location) * self.cm,
                                                     (rotation.x, rotation.y, rotation.z, rotation.w), tuple(scale))
            s.frame_move[g] = move
            s.frame_move_rot[g] = move_rot
            s.teleport[g] = teleport
        s.set_shapes([self._shapes(g) for g in range(len(self.group_props))])
        s.resolve_settings()

    def _shapes(self, g):
        """This frame's colliders for a group: its own armature's, or those of its collider sets,
        in its armature's space (Kawaii's Update*Limits, once a frame)."""
        rig, props = self._group(g)
        sources = [item.armature for item in props.collider_sets if item.armature is not None] or [rig.obj]
        shapes = []
        for armature in sources:
            for obj in collider_objects.colliders_of(armature):
                shape = collider_objects.shape_of(obj, rig.obj, self.cm)
                if shape is not None:
                    shapes.append(shape)
        return shapes

    def reset(self, scene):
        """Points back at the pose; warm-up steps if a group asks for them."""
        self._read(scene)
        s = self.system
        s.begin(s.frame_pose.copy(), s.frame_pose_rot.copy())
        for motion in self.motions:
            motion.previous = None
        self._read(scene)                     # movement relative to this frame: none
        warm = [self._group(g)[1].warm_up_frames for g in range(len(self.group_props))]
        if max(warm, default=0) > 0:
            self._warm_up(warm)
        self._write()

    def _warm_up(self, warm):
        """WarmUp (Simulation.cpp:1364): steps of the fixed step before the first frame, per group."""
        s = self.system
        saved = {}
        fixed_dt = F32(1.0) / F32(s.target_framerate)
        for k in range(max(warm)):
            for g, count in enumerate(warm):
                if count == k:
                    rows = s.group == g
                    saved[g] = (s.loc[rows].copy(), s.prev[rows].copy())
            accumulator = s.accumulator
            s.accumulator = F32(0.0)
            s.step_frame(fixed_dt, self.backend, prepare=(k == 0), call=k)
            s.accumulator = accumulator
        for g, (loc, prev) in saved.items():
            rows = s.group == g
            s.loc[rows], s.prev[rows] = loc, prev

    def step(self, scene, frames):
        self._read(scene)
        s = self.system
        dt = F32(frames * scene.render.fps_base / scene.render.fps)
        s.step_frame(dt, self.backend)
        for g in range(len(self.group_props)):
            rig, _props = self._group(g)
            location, rotation, scale = rig.obj.matrix_world.decompose()
            self.motions[g].consume(s.consume_fraction, np.array(location) * self.cm,
                                    (rotation.x, rotation.y, rotation.z, rotation.w), tuple(scale))
        self._write()

    def _write(self):
        """Rotations back onto every chain bone, and heads for bones Kawaii places directly."""
        global _writing
        s = self.system
        rotation, _turned = s.results()
        # Every bone below a group's root takes its simulated head (ApplySimulateResult sets each
        # such bone's location). Where a parent has one child its rotation already puts the head
        # there; sync bones and branching chains need the location itself.
        placed = s.parent[self.real] >= 0
        _writing = True
        self.own_update = True
        try:
            for r, rig in enumerate(self.rigs):
                rows = self.rig_of_point == r
                rig.write(self.bone_of_point[rows], rotation[self.real[rows]],
                          s.loc[self.real[rows]] / self.cm, placed[rows])
        finally:
            _writing = False

    def restore(self):
        for rig in self.rigs:
            if rig.obj.name in bpy.data.objects:
                rig.restore()

    def store(self, frame):
        if not self.cache:
            self.collider_prints = frame_cache.collider_prints(self)
        self.cache[frame] = frame_cache.Snapshot(self)

    def show_unsimulated(self):
        """A frame the cache has not reached: the chains at their input, as cloth shows them."""
        self.own_update = True
        for rig in self.rigs:
            rig.read()
            rig.restore()

    def cached_range(self):
        return (min(self.cache), max(self.cache)) if self.cache else None


def scene_wind(scene, cm):
    """The scene's wind, standing in for Unreal's wind sources: every visible Wind force field
    blows along its local Z at its Strength (Unreal's wind Speed; not converted, since Kawaii
    uses it as is). (world direction, speed), or None when nothing blows."""
    total = np.zeros(3)
    found = False
    for obj in scene.objects:
        field = obj.field
        if field is None or field.type != "WIND" or not field.strength:
            continue
        try:
            if not obj.visible_get():
                continue
        except RuntimeError:
            pass
        axis = np.array(obj.matrix_world.to_3x3().col[2])
        length = np.linalg.norm(axis)
        if length > 0:
            total += axis / length * field.strength
            found = True
    speed = float(np.linalg.norm(total))
    if not found or speed == 0.0:
        return None
    return total / speed, speed


def runtime(scene, rebuild=False):
    key = scene.as_pointer()
    current = _runtimes.get(key)
    if current is None or rebuild or key in _dirty or "all" in _dirty:
        if current is not None:
            current.restore()
        _dirty.discard(key)
        _dirty.discard("all")
        current = _runtimes[key] = Runtime(scene)
    return current


def set_simulating(scene, on):
    if on:
        rt = runtime(scene, rebuild=True)
        rt.reset(scene)
        rt.last_frame = scene.frame_current
    else:
        current = _runtimes.pop(scene.as_pointer(), None)
        if current is not None:
            current.restore()


@persistent
def _frame_changing(scene, depsgraph=None):
    """A cached frame is written before Blender evaluates it, so renders see it."""
    settings = scene.swish
    if not (settings.simulate and settings.use_cache):
        return
    key = scene.as_pointer()
    current = _runtimes.get(key)
    if current is None or key in _dirty or "all" in _dirty:
        return
    snapshot = current.cache.get(scene.frame_current)
    if snapshot is not None:
        snapshot.replay(current)


@persistent
def _frame_changed(scene, depsgraph=None):
    settings = scene.swish
    if not settings.simulate:
        return
    rt = runtime(scene)
    frame = scene.frame_current
    frames = None if rt.last_frame is None else frame - rt.last_frame
    playing_on = frames is not None and 1 <= frames <= MAX_FRAME_STEP
    if not settings.use_cache:
        if not playing_on or frame == scene.frame_start:
            rt.reset(scene)
        else:
            rt.step(scene, frames)
        rt.last_frame = frame
        return
    if rt.cache_key != frame_cache.key(scene):
        rt.cache.clear()
        rt.cache_key = frame_cache.key(scene)
    snapshot = rt.cache.get(frame)
    if snapshot is not None:
        snapshot.restore_state(rt)
        snapshot.replay(rt)
        rt.last_frame = frame
    elif frame == scene.frame_start:
        rt.reset(scene)
        rt.store(frame)
        rt.last_frame = frame
    elif playing_on and rt.last_frame in rt.cache:
        rt.cache[rt.last_frame].restore_state(rt)
        rt.step(scene, frames)
        rt.store(frame)
        rt.last_frame = frame
    else:
        rt.show_unsimulated()
        rt.last_frame = None


@persistent
def _depsgraph_updated(scene, depsgraph):
    current = _runtimes.get(scene.as_pointer())
    if current is None:
        return
    if frame_cache.relevant_update(current, depsgraph):
        current.cache.clear()


@persistent
def _file_loaded(_file):
    _runtimes.clear()
    _dirty.clear()


def register():
    if _frame_changed not in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.append(_frame_changed)
    if _frame_changing not in bpy.app.handlers.frame_change_pre:
        bpy.app.handlers.frame_change_pre.append(_frame_changing)
    if _depsgraph_updated not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_depsgraph_updated)
    if _file_loaded not in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.append(_file_loaded)


def unregister():
    for current in _runtimes.values():
        current.restore()
    _runtimes.clear()
    _dirty.clear()
    while _frame_changed in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.remove(_frame_changed)
    while _frame_changing in bpy.app.handlers.frame_change_pre:
        bpy.app.handlers.frame_change_pre.remove(_frame_changing)
    while _depsgraph_updated in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_depsgraph_updated)
    while _file_loaded in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(_file_loaded)
