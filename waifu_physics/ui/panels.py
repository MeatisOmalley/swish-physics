"""The Waifu Physics sidebar tab in the 3D viewport."""
import bpy

from ..data import colliders, curves
from ..data import links as chain_links
from ..data.props import FORCE_CHANNELS, FORCE_KINDS
from ..solver import native


def _tree_and_side(box):
    """A box's tree on the left and its + and - buttons in a column on the right, + above -."""
    body = box.row()
    return body.column(), body.column(align=True)


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
        row = layout.row(align=True)
        row.operator("waifu_physics.cache_toggle", text=f"Cached  {span[0]}-{span[1]}" if span else "Cache",
                     icon="DISK_DRIVE", depress=span is not None)
        row.operator("waifu_physics.bake", icon="KEYFRAME")          # greyed until cached (its poll)
        if native.backend() is native.step_numpy:
            layout.label(text=f"Using the slower numpy step: {native.reason()}", icon="INFO")
        tabs = layout.row()
        tabs.scale_y = 1.15
        tabs.prop(settings, "tab", expand=True)
        if settings.tab == "COLLIDERS":
            _draw_colliders(layout, context)
            return

        obj = context.object
        armatures = _tree_armatures(context)
        from . import manager
        if manager.listed(context):              # any armature with a group, selected or not
            showing = manager.is_open(context.area)
            layout.operator("waifu_physics.chain_manager", icon="OUTLINER", depress=showing,
                            text="Hide Chain Manager" if showing else "Chain Manager")
            if showing and not context.space_data.show_gizmo:
                note = layout.row()
                note.alert = True
                note.label(text="Turn on Gizmos in the header to see it", icon="ERROR")
        box = layout.box()
        header = box.row(align=True)
        header.label(text="Groups")
        header.prop(settings, "selected_only", text="", icon="RESTRICT_SELECT_OFF")
        box, side = _tree_and_side(box)
        side.enabled = obj is not None and obj.type == "ARMATURE"
        side.operator("waifu_physics.group_new", text="", icon="ADD")
        side.operator("waifu_physics.group_remove", text="", icon="REMOVE")
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
        row = layout.row(align=True)
        row.operator("waifu_physics.group_add", icon="PLUS")
        row.operator("waifu_physics.exclude", icon="X")
        if not len(obj.waifu_physics.groups):
            return
        from ..data import bone_refs
        lost = bone_refs.missing(obj)
        if lost:
            box = layout.box().column(align=True)
            box.alert = True
            row = box.split(factor=0.65)
            row.label(text=f"{len(lost)} missing bone{'' if len(lost) == 1 else 's'}", icon="ERROR")
            row.operator("waifu_physics.bones_clean_up")
            box.label(text=", ".join(lost[:3]) + (" ..." if len(lost) > 3 else ""))
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
        row.enabled = span is None                  # a cache plays what was simulated: settings wait for it to clear
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
        return (context.scene.waifu_physics.tab == "PHYSICS" and obj is not None and obj.type == "ARMATURE"
                and len(obj.waifu_physics.groups) > 0)

    @staticmethod
    def group(context):
        found = context.object.waifu_physics
        return found.groups[min(found.active_group, len(found.groups) - 1)]

    def draw(self, context):
        """Every settings panel: greyed while a cache plays, since changing settings could not change what it
        shows. The panels draw their own contents in draw_settings."""
        from ..runtime import live
        cached = live.is_cached(context.scene)
        if cached and self.bl_idname == "WAIFU_PHYSICS_PT_settings":
            self.layout.label(text="Cached: clear the cache to edit", icon="LOCKED")
        self.layout.enabled = not cached
        self.draw_settings(context)


# Settings shown the intuitive way round (display only: Kawaii's values are stored and exported).
_SHOWN = {"stiffness": "stiffness_level", "damping": "damping_level", "world_damping_location": "world_location_inertia",
          "world_damping_rotation": "world_rotation_inertia"}
_SHORT_LABELS = {"world_damping_location": "Moving", "world_damping_rotation": "Rotating"}


class WAIFU_PHYSICS_PT_settings(_GroupPanel, bpy.types.Panel):
    bl_idname = "WAIFU_PHYSICS_PT_settings"
    bl_label = "Physics"

    def draw_settings(self, context):
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
            elif name == "radius":                       # the same gap after the Inertia pair, one row
                column.split(factor=0.5).label(text="")
            split = column.split(factor=0.5, align=True)
            label = split.row()
            label.alignment = "RIGHT"
            label.label(text=_SHORT_LABELS.get(name, group.bl_rna.properties[shown].name))
            row = split.row(align=True)
            row.prop(group, shown, text="")
            row.prop(group, f"use_{name}_curve", text="", icon="FCURVE")
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

    def draw_settings(self, context):
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
        layout.prop(settings, "target_framerate")
        layout.column(heading="Live Playback").prop(settings, "fixed_substepping")


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

    def draw_settings(self, context):
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


def _collider_armatures(context):
    """The armatures the Colliders tab lists, in the scene's order: every one with colliders, and the active one,
    so it can be given its first. What is listed follows what exists, not what is selected."""
    active = colliders.armature_of(context)
    return [obj for obj in context.scene.objects
            if obj.type == "ARMATURE" and (obj == active or colliders.all_of(obj))]


class _ColliderList:
    """A list of colliders from bpy.data.objects, each saying where it hangs (a registered UIList is not
    subclassed: the subclass takes over its registration)."""

    def draw_item(self, context, layout, data, item, icon, active_data, active_property, index=0, flt_flag=0):
        found = colliders.values(item) or {}
        split = layout.split(factor=0.66, align=True)
        row = split.row(align=True)
        row.prop(item.waifu_physics_collider, "enabled", text="")
        row.prop(item, "name", text="", emboss=False,
                 icon=colliders.SHAPE_ICONS.get(found.get("Shape"), "MESH_UVSPHERE"))
        where = split.row()
        where.alignment = "RIGHT"
        where.enabled = False
        if colliders.on_simulated_bone(item):
            where.enabled, where.alert = True, True
            where.label(text=colliders.short_name(item.parent_bone), icon="ERROR")
        elif item.parent is not None:
            where.label(text=colliders.short_name(item.parent_bone) if item.parent_type == "BONE" else item.parent.name)

    def filter_items(self, context, data, propname):
        listed = set(self.listed(context))
        return [self.bitflag_filter_item if obj in listed else 0 for obj in getattr(data, propname)], []


class WAIFU_PHYSICS_UL_colliders(_ColliderList, bpy.types.UIList):
    """The colliders on one armature's bones: the armature is the list's id."""

    def listed(self, context):
        armature = bpy.data.objects.get(self.list_id)
        return colliders.all_of(armature) if armature is not None and armature.type == "ARMATURE" else []


class WAIFU_PHYSICS_UL_scene_colliders(_ColliderList, bpy.types.UIList):
    """The scene's colliders, on no armature."""

    def listed(self, context):
        return colliders.scene_colliders(context.scene, enabled_only=False)


def _folder(layout, owner, prop, text, icon, depress=False, armature=None):
    """A folder's row: its arrow, then its name (an armature's activates it, as in the Groups box)."""
    row = layout.row(align=True)
    shown = getattr(owner, prop)
    row.prop(owner, prop, text="", emboss=False, icon="DOWNARROW_HLT" if shown else "RIGHTARROW")
    left = row.row(align=True)
    left.alignment = "LEFT"
    if armature is not None:
        left.operator("waifu_physics.armature_activate", text=text, icon=icon, emboss=depress,
                      depress=depress).armature = armature.name
    else:
        left.label(text=text, icon=icon)
    return shown


def _hint(layout, text):
    row = layout.row()
    row.enabled = False
    row.label(text=text)


def _draw_colliders(layout, context):
    """The Colliders tab: a box of folders (one per armature with colliders, then the scene's) with + and - on
    its right, the picked collider's settings, and Generate. Picking a collider in a list is selecting it in
    the viewport, and the other way round; each list highlights the pick only if it holds it."""
    from ..runtime import live
    settings = context.scene.waifu_physics
    layout = layout.column()
    layout.enabled = not live.is_cached(context.scene)
    box = layout.box()
    header = box.row(align=True)
    header.label(text="Colliders")
    header.prop(settings, "show_colliders", text="", toggle=True,
                icon="HIDE_OFF" if settings.show_colliders else "HIDE_ON")
    tree, side = _tree_and_side(box)
    index = settings.active_collider
    picked = bpy.data.objects[index] if 0 <= index < len(bpy.data.objects) else None
    active = colliders.armature_of(context)
    for rig in _collider_armatures(context):
        if _folder(tree, rig.waifu_physics, "colliders_expanded", rig.name, "ARMATURE_DATA", rig == active, rig):
            found = colliders.all_of(rig)
            if found:
                tree.template_list("WAIFU_PHYSICS_UL_colliders", rig.name, bpy.data, "objects", settings,
                                   "active_collider", rows=len(found), maxrows=len(found))
            else:
                _hint(tree, "Pose Mode: select bones, then Generate")
        tree.separator(factor=0.8)
    if _folder(tree, settings, "scene_colliders_expanded", "Scene", "SCENE_DATA"):
        found = colliders.scene_colliders(context.scene, enabled_only=False)
        if found:
            tree.template_list("WAIFU_PHYSICS_UL_scene_colliders", "", bpy.data, "objects", settings,
                               "active_collider", rows=len(found), maxrows=len(found))
        else:
            _hint(tree, "+ adds a ground or a shape")
    side.menu("WAIFU_PHYSICS_MT_collider_add", text="", icon="ADD")
    remove = side.row()
    remove.enabled = colliders.is_collider(picked)
    remove.operator("waifu_physics.collider_remove", text="", icon="REMOVE").name = picked.name if picked else ""
    if colliders.is_collider(picked):
        _collider_box(layout, picked)

    box = layout.box()
    box.label(text="Generate from Selected Bones", icon="BONE_DATA")
    box.prop(settings, "collider_shape", text="")
    bones = context.selected_pose_bones or () if context.mode == "POSE" else ()
    again = any(colliders.has_collider(bone.id_data, bone.name) for bone in bones)
    row = box.row()
    row.scale_y = 1.3
    row.operator("waifu_physics.colliders_from_bones", text="Regenerate" if again else "Generate",
                 icon="FILE_REFRESH" if again else "MOD_PHYSICS").shape = settings.collider_shape
    if not bones:
        _hint(box, "Pose Mode: select the bones")


def _collider_box(layout, obj):
    """The picked collider: what it is, its shape and sizes, and a warning if its bone is simulated."""
    found = colliders.values(obj)
    box = layout.box()
    box.label(text=obj.name, icon=colliders.SHAPE_ICONS.get((found or {}).get("Shape"), "MESH_UVSPHERE"))
    col = box.column()
    col.use_property_split = True
    col.use_property_decorate = False
    if found is not None:
        md, ident = colliders.input_path(obj, "Shape")
        col.prop(getattr(md.properties.inputs, ident), "value", text="Shape")
        names = {"Box": ("Extent",), "Plane": ("Radius",), "Capsule": ("Radius", "Length"),
                 "Tapered Capsule": ("Radius", "Radius 1", "Length")}
        for name in names.get(found["Shape"], ("Radius",)):
            md, ident = colliders.input_path(obj, name)
            col.prop(getattr(md.properties.inputs, ident), "value", text=name)
    if colliders.on_simulated_bone(obj):
        warning = box.column(align=True)
        warning.alert = True
        warning.label(text="Its bone is in a chain: it chases", icon="ERROR")
        warning.label(text="the chain it pushes. Move it up the bones")


def _note(layout, text, icon="INFO"):
    row = layout.row()
    row.enabled = False
    row.label(text=text, icon=icon)


class WAIFU_PHYSICS_PT_collides_with(_GroupPanel, bpy.types.Panel):
    """What the group collides with: the colliders of the armatures it lists (its own and the one it hangs
    from, until the list is edited), and the scene's. A setting of the group, so it sits with the others."""
    bl_idname = "WAIFU_PHYSICS_PT_collides_with"
    bl_label = "Collision"
    bl_options = {"DEFAULT_CLOSED"}

    def draw_settings(self, context):
        armature = context.object
        group = self.group(context)
        layout = self.layout
        layout.label(text="Collides with the colliders of")
        col = layout.column(align=True)
        if group.custom_collider_sets or len(group.collider_sets):
            for index, item in enumerate(group.collider_sets):
                row = col.row(align=True)
                row.prop(item, "armature", text="")
                row.operator("waifu_physics.collider_set_remove", text="", icon="X").index = index
            if not len(group.collider_sets):
                _note(col, "No armatures", icon="BLANK1")
        else:                                                   # the defaults, until the list is edited
            for index, source in enumerate(colliders.default_sources(armature)):
                row = col.row(align=True)
                row.label(text="Its own armature" if source == armature else "The armature it hangs from",
                          icon="ARMATURE_DATA")
                row.operator("waifu_physics.collider_set_remove", text="", icon="X").index = index
        layout.operator("waifu_physics.collider_set_add", text="Add Armature", icon="ADD")
        layout.separator()
        layout.prop(group, "use_scene_colliders")


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


def _pair_row(layout, label, owner, *names):
    """Values sharing one label, laid out as Blender's property split lays out one: the label right-aligned in
    the same 40% (so it lines up with the rows around it and shortens as the sidebar narrows)."""
    split = layout.split(factor=0.4, align=True)
    left = split.row()
    left.alignment = "RIGHT"
    left.label(text=label)
    row = split.row(align=True)
    for name in names:
        row.prop(owner, name, text="")


class WAIFU_PHYSICS_PT_forces(_GroupPanel, bpy.types.Panel):
    bl_idname = "WAIFU_PHYSICS_PT_forces"
    bl_label = "Forces and Wind"
    bl_options = {"DEFAULT_CLOSED"}

    def draw_settings(self, context):
        from ..runtime import fields as scene_fields
        group = self.group(context)
        layout = self.layout

        # Blender's force fields, felt as Blender computes them
        layout.prop(group, "use_force_fields")
        if group.use_force_fields:
            col = layout.column()
            col.use_property_split = True
            col.use_property_decorate = False
            col.prop(group, "force_field_collection")
            col.prop(group, "force_field_strength")
            found = scene_fields.field_objects(context.scene, group.force_field_collection)
            felt = [obj for obj in found if scene_fields.spec_of(obj) is not None]
            if not found:
                row = col.row()
                row.label(text="No force fields yet", icon="INFO")
                row.operator("waifu_physics.wind_field_add", text="", icon="FORCE_WIND")
            elif len(felt) < len(found):
                note = col.row()
                note.enabled = False
                note.label(text=f"{len(found) - len(felt)} of {len(found)} fields are types the chains can't feel",
                           icon="INFO")
        layout.separator()

        # The group's own forces
        row = layout.row()
        row.template_list("WAIFU_PHYSICS_UL_forces", "", group, "forces", group, "active_force", rows=3)
        column = row.column(align=True)
        column.menu("WAIFU_PHYSICS_MT_force_add", text="", icon="ADD")
        column.operator("waifu_physics.force_remove", text="", icon="REMOVE")
        if not len(group.forces):
            return
        force = group.forces[min(group.active_force, len(group.forces) - 1)]
        box = layout.box()
        header = box.row()
        header.scale_y = 1.2
        header.prop(force, "category", text="")                # what the force is, as its header
        col = box.column()
        col.use_property_split = True
        col.use_property_decorate = False
        kind = force.kind
        if kind == "BASIC":
            col.prop(force, "space")
            col.prop(force, "direction", text="Push")
            col.prop(force, "interval", text="Pulse Every")
        elif kind == "GRAVITY":
            col.label(text="Direction")                          # a heading, as the Physics panel's Inertia
            col.prop(force, "override_direction", text="Custom")
            sub = col.column(align=True)
            sub.active = force.override_direction
            for index, axis in enumerate("XYZ"):
                sub.prop(force, "direction", index=index, text=axis)
        elif kind == "CURVE":
            col.prop(force, "space")
            col.prop(force, "amplitude")
            col.prop(force, "duration")
            col.prop(force, "time_scale")
            col.prop(force, "evaluate")
            if force.evaluate != "SINGLE":
                col.prop(force, "substeps")
            for axis, channel in zip("XYZ", FORCE_CHANNELS):
                _curve_box(box, force, channel, f"{axis} over time, -1 to 1")
        elif kind == "PROCEDURAL_WIND":
            col.operator_menu_enum("waifu_physics.wind_preset", "preset", text="Preset", icon="PRESET")
            col.prop(force, "space")
            col.prop(force, "direction")
            winds = col.column(align=True)
            winds.prop(force, "constant", text="Steady")
            for label, amount, period in (("Sway", "sway", "sway_period"), ("Ripples", "ripple", "ripple_period"),
                                          ("Flutter", "random", "random_period")):
                _pair_row(winds, label, force, amount, period)
            _pair_row(winds, "Gusts", force, "cycle_min", "cycle_max")
            _pair_row(winds, "Gust Period", force, "cycle_period")
            col.prop(force, "show_advanced")
            if force.show_advanced:
                for name in ("noise_angle", "noise_period", "time_scale", "sway_phase", "ripple_phase",
                             "ripple_delay", "cycle_phase", "seed"):
                    col.prop(force, name)
        if kind != "PROCEDURAL_WIND":
            _pair_row(col, "Strength" if kind == "GRAVITY" else "Random Scale", force, "random_min", "random_max")
        box.prop(force, "use_rate_curve", icon="FCURVE")
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

    def draw_settings(self, context):
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


CLASSES = (WAIFU_PHYSICS_UL_colliders, WAIFU_PHYSICS_UL_scene_colliders, WAIFU_PHYSICS_UL_groups, WAIFU_PHYSICS_UL_links, WAIFU_PHYSICS_UL_forces, WAIFU_PHYSICS_UL_sync, WAIFU_PHYSICS_UL_sync_targets, WAIFU_PHYSICS_PT_main,
           WAIFU_PHYSICS_PT_settings, WAIFU_PHYSICS_PT_collides_with, WAIFU_PHYSICS_PT_links, WAIFU_PHYSICS_PT_forces, WAIFU_PHYSICS_PT_sync,
           WAIFU_PHYSICS_PT_advanced)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
