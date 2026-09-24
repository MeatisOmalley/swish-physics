"""Operators: make and edit groups from the bones selected in Pose Mode."""
import json

import bpy
from bpy_extras.io_utils import ExportHelper, ImportHelper

from ..data import colliders
from ..data import links as chain_links
from ..data import curves as group_curves
from ..data import presets, serialize
from ..runtime import live


def _selected_roots(context):
    """The topmost selected bones: selecting bones acts on their whole chains."""
    selected = {pb.name for pb in context.selected_pose_bones or ()
                if pb.id_data == context.object}
    roots = []
    for name in sorted(selected):
        bone = context.object.pose.bones[name]
        parent, covered = bone.parent, False
        while parent is not None:
            if parent.name in selected:
                covered = True
                break
            parent = parent.parent
        if not covered:
            roots.append(name)
    return roots


def _descends(obj, name, ancestor):
    bone = obj.pose.bones.get(name)
    while bone is not None:
        if bone.name == ancestor:
            return True
        bone = bone.parent
    return False


def _claim(obj, roots, keep=None):
    """Take these chains out of every other group, so no bone is simulated twice."""
    for group in obj.swish.groups:
        if group is keep:
            continue
        for k in reversed(range(len(group.roots))):
            existing = group.roots[k].name
            if any(existing == root or _descends(obj, existing, root) or _descends(obj, root, existing)
                   for root in roots):
                group.roots.remove(k)


class _PoseBonesOperator:
    @classmethod
    def poll(cls, context):
        obj = context.object
        return obj is not None and obj.type == "ARMATURE" and context.mode == "POSE" \
            and bool(context.selected_pose_bones)


class SWISH_OT_group_new(_PoseBonesOperator, bpy.types.Operator):
    bl_idname = "swish.group_new"
    bl_label = "New Group"
    bl_description = "Make a group of the chains under the selected bones"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = context.object
        roots = _selected_roots(context)
        _claim(obj, roots)
        group = obj.swish.groups.add()
        group.name = roots[0] if len(roots) == 1 else f"{roots[0]} +{len(roots) - 1}"
        for name in roots:
            group.roots.add().name = name
        obj.swish.active_group = len(obj.swish.groups) - 1
        live.mark_dirty(context.scene)
        self.report({"INFO"}, f"New group with {len(roots)} chain{'s' if len(roots) != 1 else ''}")
        return {"FINISHED"}


class SWISH_OT_group_add(_PoseBonesOperator, bpy.types.Operator):
    bl_idname = "swish.group_add"
    bl_label = "Add to Group"
    bl_description = "Add the chains under the selected bones to the active group"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return super().poll(context) and len(context.object.swish.groups) > 0

    def execute(self, context):
        obj = context.object
        group = obj.swish.groups[obj.swish.active_group]
        roots = [r for r in _selected_roots(context) if r not in {root.name for root in group.roots}]
        _claim(obj, roots, keep=group)
        for name in roots:
            group.roots.add().name = name
        live.mark_dirty(context.scene)
        return {"FINISHED"}


class SWISH_OT_exclude(_PoseBonesOperator, bpy.types.Operator):
    bl_idname = "swish.exclude"
    bl_label = "Exclude Bones"
    bl_description = "Leave the selected bones, and everything under them, out of the active group"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return super().poll(context) and len(context.object.swish.groups) > 0

    def execute(self, context):
        obj = context.object
        group = obj.swish.groups[obj.swish.active_group]
        names = {bone.name for bone in group.excluded}
        for name in _selected_roots(context):
            if name not in names:
                group.excluded.add().name = name
        live.mark_dirty(context.scene)
        return {"FINISHED"}


class SWISH_OT_group_remove(bpy.types.Operator):
    bl_idname = "swish.group_remove"
    bl_label = "Remove Group"
    bl_description = "Remove the active group; its bones go back to their animation"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = context.object
        return obj is not None and obj.type == "ARMATURE" and len(obj.swish.groups) > 0

    def execute(self, context):
        obj = context.object
        live.set_simulating(context.scene, False)
        group_curves.remove(obj.swish.groups[obj.swish.active_group])
        obj.swish.groups.remove(obj.swish.active_group)
        obj.swish.active_group = max(0, obj.swish.active_group - 1)
        if context.scene.swish.simulate:
            live.set_simulating(context.scene, True)
        return {"FINISHED"}


class SWISH_OT_reset(bpy.types.Operator):
    bl_idname = "swish.reset"
    bl_label = "Reset"
    bl_description = "Start the simulation over from the current pose"

    def execute(self, context):
        if context.scene.swish.simulate:
            live.set_simulating(context.scene, True)
        return {"FINISHED"}


class SWISH_OT_collider_add(bpy.types.Operator):
    bl_idname = "swish.collider_add"
    bl_label = "Add Collider"
    bl_description = "Add a collider on the active bone: centred on it and, as a capsule, along it"
    bl_options = {"REGISTER", "UNDO"}

    shape: bpy.props.EnumProperty(name="Shape", items=[(s, s, "") for s in colliders.SHAPES], default="Capsule")

    @classmethod
    def poll(cls, context):
        obj = context.object
        return (obj is not None and obj.type == "ARMATURE" and context.mode == "POSE"
                and context.active_pose_bone is not None)

    def execute(self, context):
        colliders.add(context.object, context.active_pose_bone.name, self.shape, context)
        return {"FINISHED"}


class SWISH_OT_collider_set_add(bpy.types.Operator):
    bl_idname = "swish.collider_set_add"
    bl_label = "Add Collider Set"
    bl_description = "Collide the active group with another armature's colliders (a character's body)"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = context.object
        return obj is not None and obj.type == "ARMATURE" and len(obj.swish.groups) > 0

    def execute(self, context):
        obj = context.object
        group = obj.swish.groups[obj.swish.active_group]
        if not len(group.collider_sets):
            group.collider_sets.add().armature = obj      # its own colliders stay in
        group.collider_sets.add()
        return {"FINISHED"}


class SWISH_OT_collider_set_remove(bpy.types.Operator):
    bl_idname = "swish.collider_set_remove"
    bl_label = "Remove Collider Set"
    bl_options = {"REGISTER", "UNDO"}

    index: bpy.props.IntProperty()

    def execute(self, context):
        obj = context.object
        group = obj.swish.groups[obj.swish.active_group]
        group.collider_sets.remove(self.index)
        live.mark_dirty(context.scene)
        return {"FINISHED"}


def _chain_root(obj, group, name):
    """The group root above a bone, or None."""
    roots = {root.name for root in group.roots}
    bone = obj.pose.bones.get(name)
    while bone is not None:
        if bone.name in roots:
            return bone.name
        bone = bone.parent
    return None


class SWISH_OT_link_chains(_PoseBonesOperator, bpy.types.Operator):
    bl_idname = "swish.link_chains"
    bl_label = "Link Chains"
    bl_description = ("Link the selected chains of the active group to their neighbours, bone by bone: "
                      "a loop for a skirt, a strip for a cape")
    bl_options = {"REGISTER", "UNDO"}

    mode: bpy.props.EnumProperty(name="Mode", items=[
        ("LOOP", "Loop", "Link the last chain back to the first, all the way round (a skirt)"),
        ("STRIP", "Strip", "Link neighbours only, leaving the ends open (a cape)")], default="LOOP")

    @classmethod
    def poll(cls, context):
        return super().poll(context) and len(context.object.swish.groups) > 0

    def execute(self, context):
        obj = context.object
        group = obj.swish.groups[obj.swish.active_group]
        roots = []
        for pose_bone in context.selected_pose_bones:
            root = _chain_root(obj, group, pose_bone.name)
            if root is not None and root not in roots:
                roots.append(root)
        if len(roots) < 2:
            self.report({"WARNING"}, "Select bones in at least two chains of the active group")
            return {"CANCELLED"}
        excluded = {bone.name for bone in group.excluded}
        existing = {frozenset((link.bone_a, link.bone_b)) for link in group.links}
        added = 0
        for a, b in chain_links.pairs(obj, roots, self.mode == "LOOP", excluded):
            if frozenset((a, b)) in existing:
                continue
            link = group.links.add()
            link.bone_a, link.bone_b = a, b
            added += 1
        live.mark_dirty(context.scene)
        self.report({"INFO"}, f"{added} links between {len(roots)} chains")
        return {"FINISHED"}


class SWISH_OT_links_clear(bpy.types.Operator):
    bl_idname = "swish.links_clear"
    bl_label = "Clear Links"
    bl_description = "Remove every link of the active group"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = context.object
        return (obj is not None and obj.type == "ARMATURE" and len(obj.swish.groups) > 0
                and len(obj.swish.groups[obj.swish.active_group].links) > 0)

    def execute(self, context):
        obj = context.object
        obj.swish.groups[obj.swish.active_group].links.clear()
        live.mark_dirty(context.scene)
        return {"FINISHED"}


class SWISH_OT_link_remove(bpy.types.Operator):
    bl_idname = "swish.link_remove"
    bl_label = "Remove Link"
    bl_options = {"REGISTER", "UNDO"}

    index: bpy.props.IntProperty()

    def execute(self, context):
        obj = context.object
        obj.swish.groups[obj.swish.active_group].links.remove(self.index)
        live.mark_dirty(context.scene)
        return {"FINISHED"}


class SWISH_OT_cache_all(bpy.types.Operator):
    bl_idname = "swish.cache_all"
    bl_label = "Cache All"
    bl_description = "Simulate the whole frame range into the cache, to scrub and render"

    @classmethod
    def poll(cls, context):
        return context.scene.swish.simulate

    def execute(self, context):
        scene = context.scene
        scene.swish.use_cache = True
        original = scene.frame_current
        wm = context.window_manager
        wm.progress_begin(scene.frame_start, scene.frame_end)
        try:
            for frame in range(scene.frame_start, scene.frame_end + 1):
                scene.frame_set(frame)
                wm.progress_update(frame)
        finally:
            wm.progress_end()
        scene.frame_set(original)
        return {"FINISHED"}


class SWISH_OT_cache_clear(bpy.types.Operator):
    bl_idname = "swish.cache_clear"
    bl_label = "Clear Cache"
    bl_description = "Forget every cached frame"

    def execute(self, context):
        live.invalidate(context.scene)
        return {"FINISHED"}


def _active_group(context):
    obj = context.object
    if obj is None or obj.type != "ARMATURE" or not len(obj.swish.groups):
        return None
    return obj.swish.groups[min(obj.swish.active_group, len(obj.swish.groups) - 1)]


def _target_groups(context):
    """The active group, plus every group holding a selected bone when Edit Selected Groups is on."""
    from .selection import groups_of_selected
    targets = [_active_group(context)]
    if context.scene.swish.edit_selected_groups:
        targets += [g for g in groups_of_selected(context) if all(g != t for t in targets)]
    return targets


class _GroupOperator:
    @classmethod
    def poll(cls, context):
        return _active_group(context) is not None


class SWISH_OT_preset_apply(_GroupOperator, bpy.types.Operator):
    bl_idname = "swish.preset_apply"
    bl_label = "Apply Preset"
    bl_description = "Set the group's physics to a starting point for this kind of chain"
    bl_options = {"REGISTER", "UNDO"}

    preset: bpy.props.EnumProperty(name="Preset", items=presets.ITEMS)

    def execute(self, context):
        for group in _target_groups(context):
            presets.apply(group, self.preset)
        live.mark_dirty(context.scene)
        return {"FINISHED"}


class SWISH_OT_group_copy(_GroupOperator, bpy.types.Operator):
    bl_idname = "swish.group_copy"
    bl_label = "Copy Settings"
    bl_description = "Copy the active group's physics settings and curves (not its chains) to the clipboard"

    def execute(self, context):
        context.window_manager.clipboard = serialize.settings_text(_active_group(context))
        return {"FINISHED"}


class SWISH_OT_group_paste(_GroupOperator, bpy.types.Operator):
    bl_idname = "swish.group_paste"
    bl_label = "Paste Settings"
    bl_description = ("Paste copied physics settings and curves onto the active group "
                      "(and the groups of selected bones, with Edit Selected Groups)")
    bl_options = {"REGISTER", "UNDO"}

    text: bpy.props.StringProperty(options={"HIDDEN", "SKIP_SAVE"},
                                   description="Settings to paste instead of the clipboard's")

    def execute(self, context):
        text = self.text or context.window_manager.clipboard
        try:
            for group in _target_groups(context):
                serialize.paste(group, text)
        except ValueError as error:
            self.report({"ERROR"}, f"The clipboard holds no Swish settings ({error})")
            return {"CANCELLED"}
        live.mark_dirty(context.scene)
        return {"FINISHED"}


class SWISH_OT_setup_export(ExportHelper, bpy.types.Operator):
    bl_idname = "swish.setup_export"
    bl_label = "Export Setup"
    bl_description = "Save the armature's groups, links, curves and colliders to a JSON file"
    filename_ext = ".json"
    filter_glob: bpy.props.StringProperty(default="*.json", options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        return context.object is not None and context.object.type == "ARMATURE"

    def execute(self, context):
        data = serialize.armature_to_dict(context.object, context.scene)
        with open(self.filepath, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=1)
        self.report({"INFO"}, f"Saved {len(data['groups'])} groups, {len(data['colliders'])} colliders")
        return {"FINISHED"}


class SWISH_OT_setup_import(ImportHelper, bpy.types.Operator):
    bl_idname = "swish.setup_import"
    bl_label = "Import Setup"
    bl_description = "Replace the armature's groups and colliders with a saved setup"
    bl_options = {"REGISTER", "UNDO"}
    filename_ext = ".json"
    filter_glob: bpy.props.StringProperty(default="*.json", options={"HIDDEN"})

    include_colliders: bpy.props.BoolProperty(name="Colliders", default=True,
                                              description="Replace the armature's colliders too")
    include_scene: bpy.props.BoolProperty(name="Step Settings", default=True,
                                          description="Set the scene's steps per second and steps per frame")

    @classmethod
    def poll(cls, context):
        return context.object is not None and context.object.type == "ARMATURE"

    def execute(self, context):
        try:
            with open(self.filepath, encoding="utf-8") as handle:
                data = json.load(handle)
            warnings = serialize.armature_from_dict(context.object, data,
                                                    context.scene if self.include_scene else None,
                                                    include_colliders=self.include_colliders)
        except (OSError, ValueError, KeyError) as error:
            self.report({"ERROR"}, f"Could not load the setup: {error}")
            return {"CANCELLED"}
        for warning in warnings:
            self.report({"WARNING"}, warning)
        return {"FINISHED"}


CLASSES = (SWISH_OT_group_new, SWISH_OT_group_add, SWISH_OT_exclude, SWISH_OT_group_remove, SWISH_OT_reset,
           SWISH_OT_collider_add, SWISH_OT_collider_set_add, SWISH_OT_collider_set_remove,
           SWISH_OT_link_chains, SWISH_OT_links_clear, SWISH_OT_link_remove, SWISH_OT_cache_all,
           SWISH_OT_cache_clear, SWISH_OT_preset_apply, SWISH_OT_group_copy, SWISH_OT_group_paste,
           SWISH_OT_setup_export, SWISH_OT_setup_import)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
