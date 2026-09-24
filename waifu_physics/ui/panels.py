"""The Waifu Physics sidebar tab in the 3D viewport."""
import bpy

from ..data import colliders, curves
from ..data import links as chain_links
from ..data.props import FORCE_CHANNELS, FORCE_KINDS
from ..solver import native


def _tree_armatures(context):
    """The armatures the Groups box lists. Selected Only: every selected armature, groups or not.
    Otherwise: every armature with a group, plus the active one, so it can be given its first."""
    active = context.object if context.object is not None and context.object.type == "ARMATURE" else None
    if context.scene.waifu_physics.selected_only:
        found = [o for o in context.selected_objects if o.type == "ARMATURE"]
        if active is not None and active in found:          # the active one first
            found.remove(active)
            found.insert(0, active)
        return found
    found = [o for o in context.scene.objects if o.type == "ARMATURE" and len(o.waifu_physics.groups)]
    if active is not None and active not in found:
        found.insert(0, active)
    return found


def _plural(count, noun):
    return f"{count} {noun}{'' if count == 1 else 's'}"


class WAIFU_PHYSICS_UL_groups(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_property, index=0, flt_flag=0):
        row = layout.row(align=True)
        row.prop(item, "enabled", text="")
        row.prop(item, "name", text="", emboss=False, icon="BONE_DATA")
        count = row.row()
        count.alignment = "RIGHT"
        count.enabled = False
        count.label(text=_plural(len(item.roots), "chain"))


class WAIFU_PHYSICS_PT_main(bpy.types.Panel):
    bl_idname = "WAIFU_PHYSICS_PT_main"
    bl_label = "Waifu Physics"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Waifu Physics"

    def draw(self, context):
        from ..runtime import live
        layout = self.layout
        scene, settings = context.scene, context.scene.waifu_physics
        row = layout.row(align=True)
        row.scale_y = 1.3
        row.prop(settings, "simulate", toggle=True, icon="PHYSICS")
        row.operator("waifu_physics.reset", text="", icon="FILE_REFRESH")
        current = live._runtimes.get(scene.as_pointer())
        span = current.cached_range() if live.is_cached(scene) else None
        layout.operator("waifu_physics.cache_toggle", text=f"Cached  {span[0]}-{span[1]}" if span else "Cache",
                        icon="DISK_DRIVE", depress=span is not None)
        if native.backend() is native.step_numpy:
            layout.label(text=f"Using the slower numpy step: {native.reason()}", icon="INFO")

        obj = context.object
        armatures = _tree_armatures(context)
        box = layout.box()
        header = box.row(align=True)
        header.label(text="Groups")
        header.prop(settings, "selected_only", text="", icon="RESTRICT_SELECT_OFF")
        editing = obj is not None and obj.type == "ARMATURE"
        buttons = header.row(align=True)
        buttons.enabled = editing
        buttons.operator("waifu_physics.group_new", text="", icon="ADD")
        buttons.operator("waifu_physics.group_remove", text="", icon="REMOVE")
        if not armatures:
            box.label(text="Select an armature" if settings.selected_only else "No armature has a group yet",
                      icon="INFO")
            return
        for number, rig in enumerate(armatures):
            if number:
                box.separator(factor=0.8)            # a little space between armatures
            row = box.row(align=True)
            row.prop(rig.waifu_physics, "expanded", text="", emboss=False,
                     icon="DOWNARROW_HLT" if rig.waifu_physics.expanded else "RIGHTARROW")
            left = row.row(align=True)
            left.alignment = "LEFT"
            name = left.operator("waifu_physics.armature_activate", text=rig.name, icon="ARMATURE_DATA",
                                 emboss=rig == obj, depress=rig == obj)
            name.armature = rig.name
            if not rig.waifu_physics.expanded:
                continue
            if not len(rig.waifu_physics.groups):
                if rig == obj:                       # one hint, for the armature being edited
                    hint = box.row()
                    hint.enabled = False
                    hint.label(text="Select bones in Pose Mode, then +")
                continue
            lists = box.row()
            lists.active = rig == obj                # other armatures' lists are dimmed: not being edited
            lists.template_list("WAIFU_PHYSICS_UL_groups", rig.name, rig.waifu_physics, "groups", rig.waifu_physics, "active_group",
                                rows=len(rig.waifu_physics.groups), maxrows=len(rig.waifu_physics.groups))
        if obj is None or obj.type != "ARMATURE":
            return
        from . import manager
        showing = manager.is_open(context.area)
        layout.operator("waifu_physics.chain_manager", icon="OUTLINER", depress=showing,
                        text="Hide Chain Manager" if showing else "Chain Manager")
        if showing and not context.space_data.show_gizmo:
            note = layout.row()
            note.alert = True
            note.label(text="Turn on Gizmos in the header to see it", icon="ERROR")
        row = layout.row(align=True)
        row.operator("waifu_physics.group_add", icon="PLUS")
        row.operator("waifu_physics.exclude", icon="X")
        layout.prop(settings, "follow_selection")
        if not len(obj.waifu_physics.groups):
            return
        group = obj.waifu_physics.groups[min(obj.waifu_physics.active_group, len(obj.waifu_physics.groups) - 1)]
        constrained = chain_links.constrained_bones(obj, group)
        if constrained:
            box = layout.box().column(align=True)
            box.alert = True
            box.label(text=f"{_plural(len(constrained), 'constrained bone')}: physics can't move them",
                      icon="ERROR")
            box.label(text="Start the group below them: " + ", ".join(constrained[:3])
                           + (" ..." if len(constrained) > 3 else ""))
        row = layout.row(align=True)
        from ..data import presets
        row.menu("WAIFU_PHYSICS_MT_presets", text=presets.matching(group) or "Preset", icon="PRESET")
        row.operator("waifu_physics.preset_save", text="", icon="ADD")
        row.operator("waifu_physics.group_copy", text="", icon="COPYDOWN")
        row.operator("waifu_physics.group_paste", text="", icon="PASTEDOWN")


class _GroupPanel:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Waifu Physics"
    bl_parent_id = "WAIFU_PHYSICS_PT_main"

    @classmethod
    def poll(cls, context):
        obj = context.object
        return obj is not None and obj.type == "ARMATURE" and len(obj.waifu_physics.groups) > 0

    @staticmethod
    def group(context):
        found = context.object.waifu_physics
        return found.groups[min(found.active_group, len(found.groups) - 1)]


# Settings shown the intuitive way round (display only: Kawaii's values are stored and exported).
_SHOWN = {"stiffness": "stiffness_level", "damping": "damping_level", "world_damping_location": "movement_inertia",
          "world_damping_rotation": "turning_inertia"}
_SHORT_LABELS = {"world_damping_location": "Movement", "world_damping_rotation": "Turning"}


class WAIFU_PHYSICS_PT_settings(_GroupPanel, bpy.types.Panel):
    bl_idname = "WAIFU_PHYSICS_PT_settings"
    bl_label = "Physics"

    def draw(self, context):
        from .selection import groups_of_selected
        group = self.group(context)
        layout = self.layout
        shared = groups_of_selected(context)
        if len(shared) > 1:
            note = layout.row()
            note.enabled = False
            note.label(text=f"Edits go to all {len(shared)} selected groups", icon="INFO")
        column = layout.column(align=True)
        for name in curves.CURVED:
            shown = _SHOWN.get(name, name)
            if name == "world_damping_location":
                heading = column.split(factor=0.5)
                heading.label(text="Inertia")
            split = column.split(factor=0.5, align=True)
            label = split.row()
            label.alignment = "RIGHT"
            label.label(text=_SHORT_LABELS.get(name, group.bl_rna.properties[shown].name))
            row = split.row(align=True)
            row.prop(group, shown, text="")
            row.prop(group, f"use_{name}_curve", text="", icon="FCURVE")
            if name == "world_damping_rotation":
                column.separator(factor=0.6)             # the Inertia pair ends here
            if getattr(group, f"use_{name}_curve"):
                node = curves.node(group, name, create=False)
                if node is not None:
                    box = column.box()
                    box.label(text=("Scales Kawaii's value along the chain, root to tip" if name in _SHOWN
                                    else "Along the chain, root to tip"), icon="IPO_LINEAR")
                    box.template_curve_mapping(node, "mapping")
        layout.separator()
        column = layout.column()
        column.use_property_split = True
        column.use_property_decorate = False
        column.prop(group, "use_scene_gravity")
        if group.use_scene_gravity:
            column.prop(group, "gravity_scale")
        else:
            column.prop(group, "gravity")
            column.prop(group, "use_world_space_gravity")


class WAIFU_PHYSICS_PT_advanced(_GroupPanel, bpy.types.Panel):
    bl_idname = "WAIFU_PHYSICS_PT_advanced"
    bl_label = "Advanced"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        group = self.group(context)
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.label(text="Chain Shape")
        layout.prop(group, "dummy_bone_length")
        layout.prop(group, "bone_subdivision_count")
        sub = layout.column()
        sub.active = group.bone_subdivision_count > 0
        sub.prop(group, "bone_subdivision_collision_only")
        sub.prop(group, "bone_subdivision_densify_by_radius")
        layout.prop(group, "planar_constraint")
        if len(group.excluded):
            listed = layout.column(align=True)
            listed.label(text="Excluded Bones")
            for bone in group.excluded:
                listed.label(text=bone.name, icon="X")
        layout.separator()
        layout.label(text="Group")
        layout.prop(group, "legacy_gravity")
        layout.prop(group, "teleport_distance")
        layout.prop(group, "teleport_rotation")
        layout.prop(group, "warm_up_frames")
        settings = context.scene.waifu_physics
        layout.separator()
        layout.label(text="Scene")
        layout.prop(settings, "fixed_substepping")
        sub = layout.column()
        sub.active = settings.fixed_substepping
        sub.prop(settings, "target_framerate")
        sub.prop(settings, "max_substeps")


class WAIFU_PHYSICS_UL_links(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_property, index=0, flt_flag=0):
        row = layout.row(align=True)
        row.label(text=f"{item.bone_a}  –  {item.bone_b}", icon="CONSTRAINT_BONE")
        row.prop(item, "compliance", text="")
        row.operator("waifu_physics.link_remove", text="", icon="X", emboss=False).index = index


class WAIFU_PHYSICS_PT_links(_GroupPanel, bpy.types.Panel):
    bl_idname = "WAIFU_PHYSICS_PT_links"
    bl_label = "Links"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        group = self.group(context)
        layout = self.layout
        row = layout.row(align=True)
        row.operator("waifu_physics.link_chains", text="Link as Loop", icon="MESH_CIRCLE").mode = "LOOP"
        row.operator("waifu_physics.link_chains", text="Link as Strip", icon="IPO_LINEAR").mode = "STRIP"
        layout.template_list("WAIFU_PHYSICS_UL_links", "", group, "links", group, "active_link", rows=3)
        row = layout.row(align=True)
        row.prop(context.scene.waifu_physics, "show_links")
        row.operator("waifu_physics.links_clear", icon="TRASH")
        layout.use_property_split = True
        layout.prop(group, "compliance")
        layout.prop(group, "iterations_before_collision", text="Before Collision")
        layout.prop(group, "iterations_after_collision", text="After Collision")
        layout.prop(group, "auto_child_dummy_links")
        layout.prop(group, "bridge_count")
        sub = layout.column()
        sub.active = group.bridge_count > 0
        sub.prop(group, "bridge_feedback")


class WAIFU_PHYSICS_PT_colliders(_GroupPanel, bpy.types.Panel):
    bl_idname = "WAIFU_PHYSICS_PT_colliders"
    bl_label = "Colliders"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        group = self.group(context)
        layout = self.layout
        layout.operator_menu_enum("waifu_physics.collider_add", "shape", icon="MESH_UVSPHERE")
        box = layout.box()
        box.label(text="Collides with the colliders of:")
        if not len(group.collider_sets):
            box.label(text=context.object.name + " (its own)", icon="ARMATURE_DATA")
        for index, item in enumerate(group.collider_sets):
            row = box.row(align=True)
            row.prop(item, "armature", text="")
            row.operator("waifu_physics.collider_set_remove", text="", icon="X").index = index
        box.operator("waifu_physics.collider_set_add", icon="ADD")
        sources = [item.armature for item in group.collider_sets if item.armature] or [context.object]
        for armature in sources:
            for obj in armature.children:
                if not colliders.is_collider(obj):
                    continue
                found = colliders.values(obj)
                col = layout.box().column(align=True)
                row = col.row(align=True)
                row.prop(obj.waifu_physics_collider, "enabled", text="")
                row.label(text=f"{obj.name}  ({obj.parent_bone})", icon="MESH_UVSPHERE")
                if found is None:
                    continue
                md, ident = colliders.input_path(obj, "Shape")
                col.prop(getattr(md.properties.inputs, ident), "value", text="Shape")
                shape = found["Shape"]
                names = {"Box": ("Extent",), "Plane": ("Radius",),
                         "Capsule": ("Radius", "Length"), "Tapered Capsule": ("Radius", "Radius 1", "Length")}
                for name in names.get(shape, ("Radius",)):
                    md, ident = colliders.input_path(obj, name)
                    col.prop(getattr(md.properties.inputs, ident), "value", text=name)


def _curve_box(layout, owner, setting, label):
    node = curves.node(owner, setting, create=False)
    if node is not None:
        box = layout.box()
        box.label(text=label, icon="IPO_LINEAR")
        box.template_curve_mapping(node, "mapping")


def _filter_row(layout, force, collection, label, target):
    row = layout.row(align=True)
    names = [item.name for item in getattr(force, collection)]
    if names:
        shown = ", ".join(names[:3]) + (" ..." if len(names) > 3 else "")
    else:
        shown = "every bone" if collection == "apply_bones" else "none"
    row.label(text=f"{label}: {shown}")
    op = row.operator("waifu_physics.force_filter", text="", icon="RESTRICT_SELECT_OFF")
    op.target, op.clear = target, False
    op = row.operator("waifu_physics.force_filter", text="", icon="X")
    op.target, op.clear = target, True


class WAIFU_PHYSICS_UL_forces(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_property, index=0, flt_flag=0):
        row = layout.row(align=True)
        row.prop(item, "enabled", text="")
        kind_icon = next(entry[3] for entry in FORCE_KINDS if entry[0] == item.kind)
        row.prop(item, "name", text="", emboss=False, icon=kind_icon)


class WAIFU_PHYSICS_PT_forces(_GroupPanel, bpy.types.Panel):
    bl_idname = "WAIFU_PHYSICS_PT_forces"
    bl_label = "Forces and Wind"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        group = self.group(context)
        layout = self.layout
        col = layout.column()
        col.use_property_split = True
        col.prop(group, "simple_external_force")
        col.prop(group, "world_space_simple_external_force")
        col.prop(group, "enable_wind")
        sub = col.column()
        sub.active = group.enable_wind
        sub.prop(group, "wind_scale")
        sub.prop(group, "wind_direction_noise_angle", text="Direction Noise")
        has_field = any(o.field and o.field.type == "WIND" for o in context.scene.objects)
        if group.enable_wind and not has_field:
            row = col.row()
            row.label(text="No Wind force field in the scene", icon="INFO")
            row.operator("waifu_physics.wind_field_add", text="", icon="FORCE_WIND")
        row = layout.row()
        row.template_list("WAIFU_PHYSICS_UL_forces", "", group, "forces", group, "active_force", rows=3)
        column = row.column(align=True)
        column.operator_menu_enum("waifu_physics.force_add", "kind", text="", icon="ADD")
        column.operator("waifu_physics.force_remove", text="", icon="REMOVE")
        if not len(group.forces):
            return
        force = group.forces[min(group.active_force, len(group.forces) - 1)]
        box = layout.box()
        col = box.column()
        col.use_property_split = True
        col.prop(force, "kind")
        kind = force.kind
        if kind in ("BASIC", "CURVE", "PROCEDURAL_WIND"):
            col.prop(force, "space")
        if kind == "BASIC":
            col.prop(force, "direction", text="Push")
            col.prop(force, "interval")
        elif kind == "GRAVITY":
            col.prop(force, "override_direction")
            sub = col.column()
            sub.active = force.override_direction
            sub.prop(force, "direction")
        elif kind == "CURVE":
            col.prop(force, "amplitude")
            col.prop(force, "duration")
            col.prop(force, "time_scale")
            col.prop(force, "evaluate")
            if force.evaluate != "SINGLE":
                col.prop(force, "substeps")
            for axis, channel in zip("XYZ", FORCE_CHANNELS):
                _curve_box(box, force, channel, f"{axis} over time, -1 to 1")
        elif kind == "WIND":
            if not any(o.field and o.field.type == "WIND" for o in context.scene.objects):
                row = col.row()
                row.alert = True
                row.label(text="Blows only with a Wind force field", icon="ERROR")
                row.operator("waifu_physics.wind_field_add", text="Add", icon="FORCE_WIND")
            col.prop(force, "noise_angle")
        else:
            col.operator_menu_enum("waifu_physics.wind_preset", "preset", text="Kawaii Preset", icon="PRESET")
            col.prop(force, "direction")
            col.prop(force, "constant")
            col.prop(force, "sway")
            col.prop(force, "sway_period")
            col.prop(force, "ripple")
            col.prop(force, "ripple_period")
            col.prop(force, "cycle_min")
            col.prop(force, "cycle_max")
            col.prop(force, "cycle_period")
            col.prop(force, "random")
            col.prop(force, "random_period")
            col.prop(force, "show_advanced")
            if force.show_advanced:
                for name in ("noise_angle", "noise_period", "time_scale", "sway_phase", "ripple_phase",
                             "ripple_delay", "cycle_phase", "seed"):
                    col.prop(force, name)
        if kind != "PROCEDURAL_WIND":
            row = col.row(align=True)
            row.prop(force, "random_min")
            row.prop(force, "random_max", text="Max")
        row = box.row()
        row.prop(force, "use_rate_curve", icon="FCURVE")
        if force.use_rate_curve:
            _curve_box(box, force, "rate", "Along the chain, root to tip")
        _filter_row(box, force, "apply_bones", "Only", "APPLY")
        _filter_row(box, force, "ignore_bones", "Ignore", "IGNORE")


class WAIFU_PHYSICS_UL_sync(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_property, index=0, flt_flag=0):
        row = layout.row(align=True)
        row.prop(item, "name", text="", emboss=False, icon="CON_TRACKTO")
        row.label(text=f"{len(item.targets)} target{'s' if len(item.targets) != 1 else ''}")


class WAIFU_PHYSICS_UL_sync_targets(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_property, index=0, flt_flag=0):
        row = layout.row(align=True)
        row.label(text=item.bone, icon="BONE_DATA")
        row.prop(item, "include_children", text="", icon="OUTLINER_OB_ARMATURE")
        row.prop(item, "use_rate_curve", text="", icon="FCURVE")


class WAIFU_PHYSICS_PT_sync(_GroupPanel, bpy.types.Panel):
    bl_idname = "WAIFU_PHYSICS_PT_sync"
    bl_label = "Sync Bones"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        group = self.group(context)
        layout = self.layout
        layout.label(text="Chains follow a bone, as a skirt a thigh", icon="INFO")
        row = layout.row()
        row.template_list("WAIFU_PHYSICS_UL_sync", "", group, "sync_bones", group, "active_sync", rows=2)
        column = row.column(align=True)
        column.operator("waifu_physics.sync_add", text="", icon="ADD")
        column.operator("waifu_physics.sync_remove", text="", icon="REMOVE")
        if not len(group.sync_bones):
            return
        sync = group.sync_bones[min(group.active_sync, len(group.sync_bones) - 1)]
        box = layout.box()
        box.prop_search(sync, "bone", context.object.data, "bones", icon="BONE_DATA")
        box.label(text="Targets (bones of this group):")
        row = box.row()
        row.template_list("WAIFU_PHYSICS_UL_sync_targets", "", sync, "targets", sync, "active_target", rows=2)
        column = row.column(align=True)
        column.operator("waifu_physics.sync_target_add", text="", icon="ADD")
        column.operator("waifu_physics.sync_target_remove", text="", icon="REMOVE")
        if len(sync.targets):
            target = sync.targets[min(sync.active_target, len(sync.targets) - 1)]
            if target.use_rate_curve:
                _curve_box(box, target, "rate", f"{target.bone} to its tip")
        col = box.column()
        col.use_property_split = True
        col.prop(sync, "global_scale")
        row = col.row(align=True)
        row.prop(sync, "direction_x", text="X")
        row.prop(sync, "direction_y", text="Y")
        row.prop(sync, "direction_z", text="Z")
        col.prop(sync, "use_distance_curve")
        if sync.use_distance_curve:
            col.prop(sync, "distance")
            _curve_box(box, sync, "distance", "By the source's movement, 0 to Distance")
        col.prop(sync, "attenuation")
        sub = col.column()
        sub.active = sync.attenuation
        sub.prop(sync, "inner_radius")
        sub.prop(sync, "outer_radius")
        sub.prop(sync, "max_attenuation")


CLASSES = (WAIFU_PHYSICS_UL_groups, WAIFU_PHYSICS_UL_links, WAIFU_PHYSICS_UL_forces, WAIFU_PHYSICS_UL_sync, WAIFU_PHYSICS_UL_sync_targets, WAIFU_PHYSICS_PT_main,
           WAIFU_PHYSICS_PT_settings, WAIFU_PHYSICS_PT_links, WAIFU_PHYSICS_PT_colliders, WAIFU_PHYSICS_PT_forces, WAIFU_PHYSICS_PT_sync,
           WAIFU_PHYSICS_PT_advanced)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
