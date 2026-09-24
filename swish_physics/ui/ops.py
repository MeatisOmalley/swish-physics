"""Operators: make and edit groups from the bones selected in Pose Mode."""
import bpy

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


CLASSES = (SWISH_OT_group_new, SWISH_OT_group_add, SWISH_OT_exclude, SWISH_OT_group_remove, SWISH_OT_reset)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
