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


class SwishCollider(PropertyGroup):
    """Marks a mesh object as a Swish collider (its shape lives on its Swish Collider modifier)."""
    is_collider: BoolProperty(options={"HIDDEN"})
    enabled: BoolProperty(name="Enabled", default=True, description="This collider pushes chains")


class SwishArmature(PropertyGroup):
    groups: CollectionProperty(type=SwishGroup)
    active_group: IntProperty()


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
    follow_selection: BoolProperty(
        name="Follow Selection", default=True,
        description="Clicking a bone in Pose Mode shows its group")


def _simulate_changed(settings):
    from ..runtime import live
    live.set_simulating(bpy.context.scene, settings.simulate)


CLASSES = (SwishBoneName, SwishLink, SwishColliderSet, SwishGroup, SwishCollider, SwishArmature, SwishScene)
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
