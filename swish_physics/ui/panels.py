"""The Swish sidebar tab in the 3D viewport."""
import bpy

from ..data import curves
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


CLASSES = (SWISH_UL_groups, SWISH_PT_main, SWISH_PT_settings, SWISH_PT_chains, SWISH_PT_advanced)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
