"""What Swish stores in a .blend: groups on armature objects, and scene settings.

A group is one Kawaii Physics node. Its per-bone settings (damping, stiffness,
world damping, radius, limit angle) are animatable and are read every frame;
everything that shapes the chains -- roots, exclusions, dummies, subdivision,
links -- rebuilds the simulation when it changes. Lengths are in Blender units
in the armature's own space (Kawaii measures in its component's space).
"""
import math

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty, FloatVectorProperty,
                       IntProperty, PointerProperty, StringProperty)
from bpy.types import PropertyGroup

from ..solver.system import COMPLIANCE_TYPES

COMPLIANCE_ITEMS = [(name, name.title(), f"XPBD compliance of {name.lower()}") for name in COMPLIANCE_TYPES]
LINK_COMPLIANCE_ITEMS = [("GROUP", "Group's", "Use the group's compliance")] + COMPLIANCE_ITEMS
PLANAR_ITEMS = [("NONE", "None", "No planar constraint"),
                ("X", "X", "Keep each bone in the plane through its parent, normal to the parent's X axis"),
                ("Y", "Y", "Keep each bone in the plane through its parent, normal to the parent's Y axis"),
                ("Z", "Z", "Keep each bone in the plane through its parent, normal to the parent's Z axis")]


def _structure_changed(self, context):
    """Something that shapes the chains changed: rebuild the simulation."""
    from ..runtime import live
    live.mark_dirty()


def _result_changed(self, context):
    """A setting read every frame changed: cached frames are stale."""
    from ..runtime import live
    live.invalidate()


_propagating = False


def _setting_changed(name):
    """With Edit Selected Groups on, a change to one group's setting reaches every group
    holding a selected bone (Swish's version of Alt-editing). Any change drops cached frames."""
    def update(self, context):
        global _propagating
        from ..runtime import live
        live.invalidate()
        if _propagating or context is None or not context.scene.swish.edit_selected_groups:
            return
        from ..ui.selection import groups_of_selected
        value = getattr(self, name)
        _propagating = True
        try:
            for group in groups_of_selected(context):
                if group != self and getattr(group, name) != value:
                    setattr(group, name, value)
        finally:
            _propagating = False
    return update


def _curve_toggled(name):
    def update(self, context):
        from . import curves
        if getattr(self, f"use_{name}_curve"):
            curves.node(self, name)
        if name == "radius":                # bridge and densified points are sized from it when built
            _structure_changed(self, context)
    return update


class SwishBoneName(PropertyGroup):
    name: StringProperty()


class SwishLink(PropertyGroup):
    """A distance constraint between two bones of the group (Kawaii's bone constraint)."""
    bone_a: StringProperty(name="Bone A", update=_structure_changed)
    bone_b: StringProperty(name="Bone B", update=_structure_changed)
    compliance: EnumProperty(name="Compliance", items=LINK_COMPLIANCE_ITEMS, default="GROUP",
                             update=_structure_changed)
    exclude_from_subdivision: BoolProperty(name="No Bridge Points", update=_structure_changed,
                                           description="Place no collision points along this link")


class SwishColliderSet(PropertyGroup):
    """An armature whose colliders this group collides with."""
    armature: PointerProperty(type=bpy.types.Object, update=_structure_changed,
                              poll=lambda _self, obj: obj.type == "ARMATURE")


def _force_kind_changed(self, context):
    from . import curves
    if self.kind == "CURVE":
        for channel in FORCE_CHANNELS:
            curves.node(self, channel)
    _result_changed(self, context)


def _item_curve_toggled(name):
    def update(self, context):
        from . import curves
        if getattr(self, f"use_{name}_curve"):
            curves.node(self, name)
        _result_changed(self, context)
    return update


FORCE_CHANNELS = ("force_x", "force_y", "force_z")
FORCE_KINDS = [
    ("BASIC", "Basic", "A steady push, or a pulse every interval (Kawaii's Basic external force)", "FORCE_FORCE", 0),
    ("GRAVITY", "Gravity", "Extra gravity through velocity, in world space (Kawaii's Gravity external force)",
     "FORCE_HARMONIC", 1),
    ("CURVE", "Curve", "A push that follows curves over time (Kawaii's Curve external force)", "FCURVE", 2),
    ("WIND", "Wind", "The scene's wind force fields, per bone (Kawaii's Wind external force)", "FORCE_WIND", 3),
    ("PROCEDURAL_WIND", "Procedural Wind", "Seeded sway, ripple and gusting noise (Kawaii's Procedural Wind)",
     "MOD_WAVE", 4)]
FORCE_SPACES = [("COMPONENT", "Armature", "In the armature's space"),
                ("WORLD", "World", "In world space"),
                ("BONE", "Bone", "In each bone's own space, turning with it")]
CURVE_EVALUATE = [("SINGLE", "Single", "The curve's value at the current time"),
                  ("AVERAGE", "Average", "The average over the frame's time, in Substeps samples"),
                  ("MAX", "Max", "The largest value over the frame's time"),
                  ("MIN", "Min", "The smallest value over the frame's time")]
SYNC_DIRECTIONS = [("BOTH", "Both", "Follow movement either way along this axis"),
                   ("POSITIVE", "Positive", "Follow only movement toward +axis"),
                   ("NEGATIVE", "Negative", "Follow only movement toward -axis"),
                   ("NONE", "None", "Ignore movement along this axis")]


class SwishForce(PropertyGroup):
    """One Kawaii external force. Velocities are in Blender units a second; everything is animatable."""
    name: StringProperty(name="Name", default="Force")
    enabled: BoolProperty(name="Enabled", default=True, update=_result_changed)
    kind: EnumProperty(name="Type", items=FORCE_KINDS, default="BASIC", update=_force_kind_changed)
    space: EnumProperty(name="Space", items=FORCE_SPACES, default="WORLD", update=_result_changed)
    apply_bones: CollectionProperty(type=SwishBoneName)
    ignore_bones: CollectionProperty(type=SwishBoneName)
    random_min: FloatProperty(name="Scale Min", default=1.0, update=_result_changed,
                              description="Each frame the force is scaled by a random value in this range; "
                                          "for Gravity it is the acceleration (Blender units a second squared)")
    random_max: FloatProperty(name="Scale Max", default=1.0, update=_result_changed)
    curve_key: StringProperty(options={"HIDDEN"})
    use_rate_curve: BoolProperty(name="Rate Curve", update=_item_curve_toggled("rate"),
                                 description="Scale the force along each chain, root to tip")

    direction: FloatVectorProperty(name="Direction", default=(0.0, 0.0, 0.0), size=3, update=_result_changed,
                                   description="Basic: the push, in Blender units a second. Gravity and "
                                               "Procedural Wind: a direction")
    interval: FloatProperty(name="Interval", default=0.0, min=0.0, subtype="TIME_ABSOLUTE", unit="TIME_ABSOLUTE",
                            update=_result_changed, description="Push once every this long; 0 pushes constantly")
    override_direction: BoolProperty(name="Override Direction", update=_result_changed,
                                     description="Pull along Direction instead of straight down")
    duration: FloatProperty(name="Duration", default=1.0, min=0.001, subtype="TIME_ABSOLUTE",
                            unit="TIME_ABSOLUTE", update=_result_changed,
                            description="How long the curves run before repeating")
    amplitude: FloatVectorProperty(name="Amplitude", default=(1.0, 1.0, 1.0), size=3, update=_result_changed,
                                   description="The push at a curve value of 1, per axis, in Blender units a second")
    time_scale: FloatProperty(name="Time Scale", default=1.0, update=_result_changed)
    evaluate: EnumProperty(name="Evaluate", items=CURVE_EVALUATE, default="SINGLE", update=_result_changed)
    substeps: IntProperty(name="Substeps", default=10, min=1, max=100, update=_result_changed)
    noise_angle: FloatProperty(name="Direction Noise", default=0.0, min=0.0, max=math.pi, subtype="ANGLE",
                               update=_result_changed, description="Random turn of the wind's direction")
    noise_period: FloatProperty(name="Noise Period", default=1.0, min=0.01, update=_result_changed)
    constant: FloatProperty(name="Constant", default=0.0, update=_result_changed,
                            description="Steady wind, in Blender units a second")
    sway: FloatProperty(name="Sway", default=0.0, update=_result_changed,
                        description="Back-and-forth wind, in Blender units a second")
    sway_period: FloatProperty(name="Sway Period", default=1.0, min=0.01, update=_result_changed)
    sway_phase: FloatProperty(name="Sway Phase", default=0.0, subtype="ANGLE", update=_result_changed)
    ripple: FloatProperty(name="Ripple", default=0.0, update=_result_changed,
                          description="A wave running root to tip, in Blender units a second")
    ripple_period: FloatProperty(name="Ripple Period", default=1.0, min=0.01, update=_result_changed)
    ripple_phase: FloatProperty(name="Ripple Phase", default=0.0, subtype="ANGLE", update=_result_changed)
    ripple_delay: FloatProperty(name="Ripple Tip Delay", default=math.pi, subtype="ANGLE", update=_result_changed,
                                description="How far behind the root the tip's wave runs")
    cycle_min: FloatProperty(name="Strength Min", default=1.0, update=_result_changed)
    cycle_max: FloatProperty(name="Strength Max", default=1.0, update=_result_changed)
    cycle_period: FloatProperty(name="Strength Period", default=10.0, min=0.01, update=_result_changed)
    cycle_phase: FloatProperty(name="Strength Phase", default=0.0, subtype="ANGLE", update=_result_changed)
    random: FloatProperty(name="Random", default=0.0, update=_result_changed,
                          description="Seeded noise, in Blender units a second")
    random_period: FloatProperty(name="Random Period", default=0.5, min=0.01, update=_result_changed)
    seed: IntProperty(name="Seed", default=0, update=_result_changed)
    show_advanced: BoolProperty(name="Advanced", default=False)


class SwishSyncTarget(PropertyGroup):
    bone: StringProperty(name="Bone", update=_result_changed)
    include_children: BoolProperty(name="Children", default=True, update=_result_changed,
                                   description="Move the bones under it too")
    curve_key: StringProperty(options={"HIDDEN"})
    use_rate_curve: BoolProperty(name="Rate Curve", update=_item_curve_toggled("rate"),
                                 description="Scale the movement from this bone (0) to its chain's tip (1)")


class SwishSyncBone(PropertyGroup):
    """Kawaii's SyncBone: a bone outside the chains (a thigh) whose movement carries chain bones' poses."""
    name: StringProperty(name="Name", default="Sync")
    bone: StringProperty(name="Source Bone", update=_result_changed,
                         description="The bone whose movement from its rest position the targets follow")
    targets: CollectionProperty(type=SwishSyncTarget)
    active_target: IntProperty()
    global_scale: FloatVectorProperty(name="Scale", default=(1.0, 1.0, 1.0), size=3, update=_result_changed)
    curve_key: StringProperty(options={"HIDDEN"})
    use_distance_curve: BoolProperty(name="Distance Curve", update=_item_curve_toggled("distance"),
                                     description="Scale by how far the source has moved, from 0 to Distance")
    distance: FloatProperty(name="Distance", default=0.3, min=0.001, subtype="DISTANCE", update=_result_changed,
                            description="The movement the distance curve's right end stands for")
    direction_x: EnumProperty(name="X", items=SYNC_DIRECTIONS, default="BOTH", update=_result_changed)
    direction_y: EnumProperty(name="Y", items=SYNC_DIRECTIONS, default="BOTH", update=_result_changed)
    direction_z: EnumProperty(name="Z", items=SYNC_DIRECTIONS, default="BOTH", update=_result_changed)
    attenuation: BoolProperty(name="Distance Attenuation", update=_result_changed,
                              description="Weaken the effect on bones far from the source")
    inner_radius: FloatProperty(name="Inner Radius", default=0.0, min=0.0, subtype="DISTANCE",
                                update=_result_changed)
    outer_radius: FloatProperty(name="Outer Radius", default=0.0, min=0.0, subtype="DISTANCE",
                                update=_result_changed)
    max_attenuation: FloatProperty(name="Max Attenuation", default=1.0, min=0.0, update=_result_changed)


SETTLE_OFF = 1000.0                  # settle time shown for no stiffness: the chain never returns


def _step_rate():
    scene = getattr(bpy.context, "scene", None)
    return scene.swish.target_framerate if scene is not None else 60


def _settle_get(self):
    """Kawaii's stiffness as the seconds to get 95% of the way back to the pose: stiffness is the
    fraction of the gap closed each step (ApplyStiffnessPull), at the simulation rate."""
    s, rate = self.stiffness, _step_rate()
    if s <= 0.0:
        return SETTLE_OFF
    if s >= 1.0:
        return 1.0 / rate
    return min(SETTLE_OFF, math.log(0.05) / (rate * math.log(1.0 - s)))


def _settle_set(self, value):
    if value >= SETTLE_OFF:
        self.stiffness = 0.0
        return
    self.stiffness = 1.0 - 0.05 ** (1.0 / (_step_rate() * max(value, 1.0e-4)))


class SwishGroup(PropertyGroup):
    """One Kawaii Physics node: the chains under its root bones, and how they move."""
    name: StringProperty(name="Name", default="Group")
    enabled: BoolProperty(name="Enabled", default=True, update=_structure_changed)
    roots: CollectionProperty(type=SwishBoneName)
    excluded: CollectionProperty(type=SwishBoneName)
    links: CollectionProperty(type=SwishLink)
    collider_sets: CollectionProperty(type=SwishColliderSet)
    active_link: IntProperty()

    # FKawaiiPhysicsSettings, animatable. Radius is a length; limit angle an angle.
    damping: FloatProperty(name="Damping", default=0.1, min=0.0, max=1.0, update=_setting_changed("damping"),
                           description="How much of its velocity a point loses each step")
    stiffness: FloatProperty(name="Stiffness", default=0.05, min=0.0, max=1.0, update=_setting_changed("stiffness"),
                             description="How strongly a point is pulled back toward its animated pose")
    settle_time: FloatProperty(
        name="Settle Time (s)", get=_settle_get, set=_settle_set, min=0.0, max=SETTLE_OFF, soft_min=0.02,
        soft_max=10.0, precision=2, step=10, options=set(),
        description="Stiffness as the seconds a bone takes to get 95% of the way back to its pose. "
                    "Shorter is stiffer. It sets Kawaii's stiffness, which is what is saved, keyed and exported")
    world_damping_location: FloatProperty(
        name="World Damping Location", default=0.8, min=0.0, max=1.0,
        update=_setting_changed("world_damping_location"),
        description="How little the chains feel the armature object moving: 0 trails fully, 1 rides along")
    world_damping_rotation: FloatProperty(
        name="World Damping Rotation", default=0.8, min=0.0, max=1.0,
        update=_setting_changed("world_damping_rotation"),
        description="How little the chains feel the armature object turning: 0 trails fully, 1 rides along")
    radius: FloatProperty(name="Radius", default=0.03, min=0.0, subtype="DISTANCE", precision=4,
                          update=_setting_changed("radius"), description="Each point's collision radius")
    limit_angle: FloatProperty(name="Limit Angle", default=0.0, min=0.0, max=math.pi, subtype="ANGLE",
                               update=_setting_changed("limit_angle"),
                               description="How far a bone may swing from its animated direction; 0 for no limit")
    # Curves along the chain, root to tip, multiplying each setting (Kawaii's *CurveData).
    curve_key: StringProperty(options={"HIDDEN"})
    use_damping_curve: BoolProperty(name="Damping Curve", update=_curve_toggled("damping"))
    use_stiffness_curve: BoolProperty(name="Stiffness Curve", update=_curve_toggled("stiffness"))
    use_world_damping_location_curve: BoolProperty(name="World Damping Location Curve",
                                                   update=_curve_toggled("world_damping_location"))
    use_world_damping_rotation_curve: BoolProperty(name="World Damping Rotation Curve",
                                                   update=_curve_toggled("world_damping_rotation"))
    use_radius_curve: BoolProperty(name="Radius Curve", update=_curve_toggled("radius"))
    use_limit_angle_curve: BoolProperty(name="Limit Angle Curve", update=_curve_toggled("limit_angle"))

    gravity: FloatVectorProperty(name="Gravity", default=(0.0, 0.0, -1.0), subtype="ACCELERATION", size=3,
                                 update=_result_changed,
                                 description="Gravity; with Use Scene Gravity, a direction scaled by the scene's")
    use_scene_gravity: BoolProperty(name="Use Scene Gravity", default=True, update=_result_changed,
                                    description="Scale Gravity by the scene's gravity (Kawaii: use the project's)")
    use_world_space_gravity: BoolProperty(name="World Space Gravity", default=True, update=_result_changed,
                                          description="Gravity is in world space, not the armature's")
    legacy_gravity: BoolProperty(name="Legacy Gravity", default=False, update=_structure_changed,
                                 description="Kawaii's older gravity: added to position, not velocity")

    dummy_bone_length: FloatProperty(name="Tip Length", default=0.0, min=0.0, subtype="DISTANCE",
                                     update=_structure_changed,
                                     description="A point past each chain's last bone, so the last bone swings too")
    bone_subdivision_count: IntProperty(name="Subdivisions", default=0, min=0, max=10, update=_structure_changed,
                                        description="Extra collision points along each bone")
    bone_subdivision_collision_only: BoolProperty(name="Collision Only", default=True, update=_structure_changed,
                                                  description="Subdivision points only collide; they are not simulated")
    bone_subdivision_densify_by_radius: BoolProperty(name="Densify by Radius", default=False,
                                                     update=_structure_changed,
                                                     description="Add points until they cover the bone at this radius")
    planar_constraint: EnumProperty(name="Planar Constraint", items=PLANAR_ITEMS, default="NONE",
                                    update=_structure_changed)
    compliance: EnumProperty(name="Link Compliance", items=COMPLIANCE_ITEMS, default="LEATHER",
                             update=_structure_changed,
                             description="How much links stretch: the stiffest is concrete, the softest fat")
    iterations_before_collision: IntProperty(name="Iterations Before Collision", default=1, min=0, max=20,
                                             update=_structure_changed)
    iterations_after_collision: IntProperty(name="Iterations After Collision", default=1, min=0, max=20,
                                            update=_structure_changed)
    auto_child_dummy_links: BoolProperty(name="Link Tips", default=True, update=_structure_changed,
                                         description="Links between bones also link their tip and subdivision points")
    bridge_count: IntProperty(name="Bridge Points", default=0, min=0, max=10, update=_structure_changed,
                              description="Collision points along each link, so colliders cannot pass between chains")
    bridge_feedback: FloatProperty(name="Bridge Feedback", default=1.0, min=0.0, max=2.0,
                                   update=_structure_changed,
                                   description="How strongly a bridge point's collision pushes the bones it links")

    teleport_distance: FloatProperty(name="Teleport Distance", default=3.0, min=0.0, subtype="DISTANCE",
                                     update=_result_changed,
                                     description="An armature jumping further than this in a frame is a teleport")
    teleport_rotation: FloatProperty(name="Teleport Rotation", default=math.radians(10.0), min=0.0,
                                     update=_result_changed,
                                     subtype="ANGLE",
                                     description="An armature turning further than this in a frame is a teleport")
    warm_up_frames: IntProperty(name="Warm Up Frames", default=0, min=0, max=500, update=_result_changed,
                                description="Steps simulated before the first frame, so chains start settled")

    forces: CollectionProperty(type=SwishForce)
    active_force: IntProperty()
    sync_bones: CollectionProperty(type=SwishSyncBone)
    active_sync: IntProperty()
    simple_external_force: FloatVectorProperty(
        name="Simple Force", default=(0.0, 0.0, 0.0), size=3, subtype="VELOCITY", update=_result_changed,
        description="A constant push on every bone, in Blender units a second (Kawaii's SimpleExternalForce)")
    world_space_simple_external_force: BoolProperty(name="World Space", default=True, update=_result_changed)
    enable_wind: BoolProperty(name="Scene Wind", default=False, update=_result_changed,
                              description="Blow with the scene's wind force fields, gusting at random "
                                          "(Kawaii's Enable Wind); a field's Strength is its speed")
    wind_scale: FloatProperty(name="Wind Scale", default=1.0, update=_result_changed)
    wind_direction_noise_angle: FloatProperty(name="Wind Direction Noise", default=0.0, min=0.0, max=math.pi,
                                              subtype="ANGLE", update=_result_changed)


class SwishCollider(PropertyGroup):
    """Marks a mesh object as a Swish collider (its shape lives on its Swish Collider modifier)."""
    is_collider: BoolProperty(options={"HIDDEN"})
    enabled: BoolProperty(name="Enabled", default=True, description="This collider pushes chains")


class SwishArmature(PropertyGroup):
    groups: CollectionProperty(type=SwishGroup)
    active_group: IntProperty()
    expanded: BoolProperty(name="Expanded", default=True, description="Show this armature's groups")


class SwishScene(PropertyGroup):
    simulate: BoolProperty(name="Simulate", default=False, update=lambda self, context: _simulate_changed(self),
                           description="Simulate every Swish group in the scene while the timeline plays")
    target_framerate: IntProperty(name="Steps per Second", default=60, min=1, max=480, update=_structure_changed,
                                  description="Fixed simulation rate (Kawaii's target framerate)")
    max_substeps: IntProperty(name="Max Steps per Frame", default=4, min=1, max=32, update=_structure_changed,
                              description="Most simulation steps one frame may take; the rest of a long frame is dropped")
    fixed_substepping: BoolProperty(name="Fixed Steps", default=True, update=_structure_changed,
                                    description="Step at a fixed rate; off steps once per frame (Kawaii's legacy mode)")
    edit_selected_groups: BoolProperty(
        name="Edit Selected Groups", default=True,
        description="Changing a setting changes it in every group holding a selected bone")
    use_cache: BoolProperty(name="Cache", default=False, update=_result_changed,
                            description="Keep each simulated frame, to scrub and render without re-simulating")
    show_links: BoolProperty(name="Show Links", default=True, description="Draw every group's links in the viewport")
    stiffness_as_time: BoolProperty(
        name="Stiffness as Settle Time", default=True,
        description="Show stiffness as the seconds to settle back to the pose; off shows Kawaii's value")
    selected_only: BoolProperty(
        name="Selected Only", default=True,
        description="List the selected armature's groups only; off lists every armature with a group")
    follow_selection: BoolProperty(
        name="Follow Selection", default=True,
        description="Clicking a bone in Pose Mode shows its group")


def _simulate_changed(settings):
    from ..runtime import live
    live.set_simulating(bpy.context.scene, settings.simulate)


CLASSES = (SwishBoneName, SwishLink, SwishColliderSet, SwishForce, SwishSyncTarget, SwishSyncBone, SwishGroup,
           SwishCollider, SwishArmature, SwishScene)
SETTING_NAMES = ("damping", "stiffness", "world_damping_location", "world_damping_rotation", "radius", "limit_angle")


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Object.swish = PointerProperty(type=SwishArmature)
    bpy.types.Object.swish_collider = PointerProperty(type=SwishCollider)
    bpy.types.Scene.swish = PointerProperty(type=SwishScene)


def unregister():
    del bpy.types.Scene.swish
    del bpy.types.Object.swish_collider
    del bpy.types.Object.swish
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
