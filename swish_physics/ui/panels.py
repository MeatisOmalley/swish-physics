"""The Swish sidebar tab in the 3D viewport."""
import bpy

from ..data import colliders, curves
from ..data.props import FORCE_CHANNELS, FORCE_KINDS
from ..solver import native


class SWISH_UL_groups(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_property, index=0, flt_flag=0):
        row = layout.row(align=True)
        row.prop(item, "enabled", text="")
        row.prop(item, "name", text="", emboss=False, icon="BONE_DATA")
        row.label(text=f"{len(item.roots)}")


class SWISH_PT_main(bpy.types.Panel):
    bl_idname = "SWISH_PT_main"
    bl_label = "Swish Physics"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Swish"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.swish
        row = layout.row(align=True)
        row.scale_y = 1.3
        row.prop(settings, "simulate", toggle=True, icon="PHYSICS")
        row.operator("swish.reset", text="", icon="FILE_REFRESH")
        row = layout.row(align=True)
        row.prop(settings, "use_cache", toggle=True, icon="DISK_DRIVE")
        row.operator("swish.cache_all", icon="RENDER_ANIMATION")
        row.operator("swish.cache_clear", text="", icon="TRASH")
        if settings.use_cache and settings.simulate:
            from ..runtime import live
            current = live._runtimes.get(context.scene.as_pointer())
            span = current.cached_range() if current is not None else None
            layout.label(text=f"Cached frames {span[0]}-{span[1]}" if span else
                         f"Nothing cached: play from frame {context.scene.frame_start}, or Cache All", icon="INFO")
        if native.backend() is native.step_numpy:
            layout.label(text=f"Using the slower numpy step: {native.reason()}", icon="INFO")
        obj = context.object
        if obj is None or obj.type != "ARMATURE":
            layout.label(text="Select an armature", icon="ARMATURE_DATA")
            return
        swish = obj.swish
        row = layout.row()
        row.template_list("SWISH_UL_groups", "", swish, "groups", swish, "active_group", rows=3)
        column = row.column(align=True)
        column.operator("swish.group_new", text="", icon="ADD")
        column.operator("swish.group_remove", text="", icon="REMOVE")
        row = layout.row(align=True)
        row.operator("swish.group_add", icon="PLUS")
        row.operator("swish.exclude", icon="X")
        layout.prop(settings, "follow_selection")
        if not len(swish.groups):
            layout.label(text="Select bones in Pose Mode, then New Group", icon="INFO")
        else:
            row = layout.row(align=True)
            row.operator_menu_enum("swish.preset_apply", "preset", text="Preset", icon="PRESET")
            row.operator("swish.group_copy", text="", icon="COPYDOWN")
            row.operator("swish.group_paste", text="", icon="PASTEDOWN")
        row = layout.row(align=True)
        row.operator("swish.setup_import", icon="IMPORT")
        row.operator("swish.setup_export", icon="EXPORT")


class _GroupPanel:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Swish"
    bl_parent_id = "SWISH_PT_main"

    @classmethod
    def poll(cls, context):
        obj = context.object
        return obj is not None and obj.type == "ARMATURE" and len(obj.swish.groups) > 0

    @staticmethod
    def group(context):
        swish = context.object.swish
        return swish.groups[min(swish.active_group, len(swish.groups) - 1)]


class SWISH_PT_settings(_GroupPanel, bpy.types.Panel):
    bl_idname = "SWISH_PT_settings"
    bl_label = "Physics"

    def draw(self, context):
        group = self.group(context)
        layout = self.layout
        layout.prop(context.scene.swish, "edit_selected_groups")
        for name in curves.CURVED:
            row = layout.row(align=True)
            row.prop(group, name)
            row.prop(group, f"use_{name}_curve", text="", icon="FCURVE")
            if getattr(group, f"use_{name}_curve"):
                node = curves.node(group, name, create=False)
                if node is not None:
                    box = layout.box()
                    box.label(text="Along the chain, root to tip", icon="IPO_LINEAR")
                    box.template_curve_mapping(node, "mapping")
        layout.separator()
        layout.use_property_split = True
        layout.prop(group, "gravity")
        layout.prop(group, "use_scene_gravity")
        layout.prop(group, "use_world_space_gravity")


class SWISH_PT_chains(_GroupPanel, bpy.types.Panel):
    bl_idname = "SWISH_PT_chains"
    bl_label = "Chains"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        group = self.group(context)
        layout = self.layout
        column = layout.column(align=True)
        for root in group.roots:
            column.label(text=root.name, icon="BONE_DATA")
        if len(group.excluded):
            layout.label(text="Excluded:")
            column = layout.column(align=True)
            for bone in group.excluded:
                column.label(text=bone.name, icon="X")
        layout.use_property_split = True
        layout.prop(group, "dummy_bone_length")
        layout.prop(group, "bone_subdivision_count")
        sub = layout.column()
        sub.active = group.bone_subdivision_count > 0
        sub.prop(group, "bone_subdivision_collision_only")
        sub.prop(group, "bone_subdivision_densify_by_radius")
        layout.prop(group, "planar_constraint")


class SWISH_PT_advanced(_GroupPanel, bpy.types.Panel):
    bl_idname = "SWISH_PT_advanced"
    bl_label = "Advanced"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        group = self.group(context)
        layout = self.layout
        layout.use_property_split = True
        layout.prop(group, "legacy_gravity")
        layout.prop(group, "teleport_distance")
        layout.prop(group, "teleport_rotation")
        layout.prop(group, "warm_up_frames")
        settings = context.scene.swish
        layout.separator()
        layout.label(text="Scene")
        layout.prop(settings, "fixed_substepping")
        sub = layout.column()
        sub.active = settings.fixed_substepping
        sub.prop(settings, "target_framerate")
        sub.prop(settings, "max_substeps")


class SWISH_UL_links(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_property, index=0, flt_flag=0):
        row = layout.row(align=True)
        row.label(text=f"{item.bone_a}  –  {item.bone_b}", icon="CONSTRAINT_BONE")
        row.prop(item, "compliance", text="")
        row.operator("swish.link_remove", text="", icon="X", emboss=False).index = index


class SWISH_PT_links(_GroupPanel, bpy.types.Panel):
    bl_idname = "SWISH_PT_links"
    bl_label = "Links"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        group = self.group(context)
        layout = self.layout
        row = layout.row(align=True)
        row.operator("swish.link_chains", text="Link as Loop", icon="MESH_CIRCLE").mode = "LOOP"
        row.operator("swish.link_chains", text="Link as Strip", icon="IPO_LINEAR").mode = "STRIP"
        layout.template_list("SWISH_UL_links", "", group, "links", group, "active_link", rows=3)
        row = layout.row(align=True)
        row.prop(context.scene.swish, "show_links")
        row.operator("swish.links_clear", icon="TRASH")
        layout.use_property_split = True
        layout.prop(group, "compliance")
        layout.prop(group, "iterations_before_collision", text="Before Collision")
        layout.prop(group, "iterations_after_collision", text="After Collision")
        layout.prop(group, "auto_child_dummy_links")
        layout.prop(group, "bridge_count")
        sub = layout.column()
        sub.active = group.bridge_count > 0
        sub.prop(group, "bridge_feedback")


class SWISH_PT_colliders(_GroupPanel, bpy.types.Panel):
    bl_idname = "SWISH_PT_colliders"
    bl_label = "Colliders"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        group = self.group(context)
        layout = self.layout
        layout.operator_menu_enum("swish.collider_add", "shape", icon="MESH_UVSPHERE")
        box = layout.box()
        box.label(text="Collides with the colliders of:")
        if not len(group.collider_sets):
            box.label(text=context.object.name + " (its own)", icon="ARMATURE_DATA")
        for index, item in enumerate(group.collider_sets):
            row = box.row(align=True)
            row.prop(item, "armature", text="")
            row.operator("swish.collider_set_remove", text="", icon="X").index = index
        box.operator("swish.collider_set_add", icon="ADD")
        sources = [item.armature for item in group.collider_sets if item.armature] or [context.object]
        for armature in sources:
            for obj in armature.children:
                if not colliders.is_collider(obj):
                    continue
                found = colliders.values(obj)
                col = layout.box().column(align=True)
                row = col.row(align=True)
                row.prop(obj.swish_collider, "enabled", text="")
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
    op = row.operator("swish.force_filter", text="", icon="RESTRICT_SELECT_OFF")
    op.target, op.clear = target, False
    op = row.operator("swish.force_filter", text="", icon="X")
    op.target, op.clear = target, True


class SWISH_UL_forces(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_property, index=0, flt_flag=0):
        row = layout.row(align=True)
        row.prop(item, "enabled", text="")
        kind_icon = next(entry[3] for entry in FORCE_KINDS if entry[0] == item.kind)
        row.prop(item, "name", text="", emboss=False, icon=kind_icon)


class SWISH_PT_forces(_GroupPanel, bpy.types.Panel):
    bl_idname = "SWISH_PT_forces"
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
        if group.enable_wind and not any(o.field and o.field.type == "WIND" for o in context.scene.objects):
            col.label(text="Add a Wind force field to the scene to blow", icon="INFO")
        row = layout.row()
        row.template_list("SWISH_UL_forces", "", group, "forces", group, "active_force", rows=3)
        column = row.column(align=True)
        column.operator_menu_enum("swish.force_add", "kind", text="", icon="ADD")
        column.operator("swish.force_remove", text="", icon="REMOVE")
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
            col.prop(force, "noise_angle")
        else:
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


class SWISH_UL_sync(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_property, index=0, flt_flag=0):
        row = layout.row(align=True)
        row.prop(item, "name", text="", emboss=False, icon="CON_TRACKTO")
        row.label(text=f"{len(item.targets)} target{'s' if len(item.targets) != 1 else ''}")


class SWISH_UL_sync_targets(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_property, index=0, flt_flag=0):
        row = layout.row(align=True)
        row.label(text=item.bone, icon="BONE_DATA")
        row.prop(item, "include_children", text="", icon="OUTLINER_OB_ARMATURE")
        row.prop(item, "use_rate_curve", text="", icon="FCURVE")


class SWISH_PT_sync(_GroupPanel, bpy.types.Panel):
    bl_idname = "SWISH_PT_sync"
    bl_label = "Sync Bones"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        group = self.group(context)
        layout = self.layout
        layout.label(text="Chains follow a bone, as a skirt a thigh", icon="INFO")
        row = layout.row()
        row.template_list("SWISH_UL_sync", "", group, "sync_bones", group, "active_sync", rows=2)
        column = row.column(align=True)
        column.operator("swish.sync_add", text="", icon="ADD")
        column.operator("swish.sync_remove", text="", icon="REMOVE")
        if not len(group.sync_bones):
            return
        sync = group.sync_bones[min(group.active_sync, len(group.sync_bones) - 1)]
        box = layout.box()
        box.prop_search(sync, "bone", context.object.data, "bones", icon="BONE_DATA")
        box.label(text="Targets (bones of this group):")
        row = box.row()
        row.template_list("SWISH_UL_sync_targets", "", sync, "targets", sync, "active_target", rows=2)
        column = row.column(align=True)
        column.operator("swish.sync_target_add", text="", icon="ADD")
        column.operator("swish.sync_target_remove", text="", icon="REMOVE")
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


CLASSES = (SWISH_UL_groups, SWISH_UL_links, SWISH_UL_forces, SWISH_UL_sync, SWISH_UL_sync_targets, SWISH_PT_main,
           SWISH_PT_settings, SWISH_PT_chains, SWISH_PT_links, SWISH_PT_colliders, SWISH_PT_forces, SWISH_PT_sync,
           SWISH_PT_advanced)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
