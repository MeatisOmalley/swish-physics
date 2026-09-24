"""Operators: make and edit groups from the bones selected in Pose Mode."""
import json

import bpy
from bpy_extras.io_utils import ExportHelper, ImportHelper

from ..data import colliders
from ..data import links as chain_links
from ..data import curves as group_curves
from ..data import presets, serialize
from ..data.props import FORCE_CHANNELS, FORCE_KINDS
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
    """Take these chains out of every other group, so no bone is simulated twice. A group left
    with no chains is removed."""
    emptied = []
    for group in obj.swish.groups:
        if group is keep:
            continue
        before = len(group.roots)
        for k in reversed(range(len(group.roots))):
            existing = group.roots[k].name
            if any(existing == root or _descends(obj, existing, root) or _descends(obj, root, existing)
                   for root in roots):
                group.roots.remove(k)
        if before and not len(group.roots):
            emptied.append(group.name)
    for name in emptied:
        index = [g.name for g in obj.swish.groups].index(name)
        group_curves.remove_owned(obj.swish.groups[index])
        obj.swish.groups.remove(index)
    if emptied:
        obj.swish.active_group = max(0, min(obj.swish.active_group, len(obj.swish.groups) - 1))


class _PoseBonesOperator:
    @classmethod
    def poll(cls, context):
        obj = context.object
        return obj is not None and obj.type == "ARMATURE" and context.mode == "POSE" \
            and bool(context.selected_pose_bones)


def group_name(obj, roots):
    """A readable name for a new group: what its chains' names share, VRoid's J_Sec_ prefix and the
    numbering dropped ("J_Sec_Hair1_01" .. "J_Sec_Hair24_01" -> "Hair"), made unique on the armature."""
    import os
    import re
    stripped = [re.sub(r"^J_(Sec|Bip|Adj)_", "", name) for name in roots]
    shared = re.sub(r"[\d_.\s-]+$", "", os.path.commonprefix(stripped))
    if len(shared) < 2:
        shared = re.sub(r"[\d_.\s-]+$", "", stripped[0]) or "Group"
    name, taken, number = shared, {g.name for g in obj.swish.groups}, 2
    while name in taken:
        name, number = f"{shared} {number}", number + 1
    return name


def make_active(context, obj):
    """Make an armature the one the panels edit, keeping Pose Mode if the user was in it."""
    view_layer = context.view_layer
    if view_layer.objects.active == obj or obj.name not in view_layer.objects:
        return
    posing = context.mode == "POSE"
    if posing and obj.mode != "POSE":
        bpy.ops.object.mode_set(mode="OBJECT")
    view_layer.objects.active = obj
    obj.select_set(True)
    if posing and obj.mode != "POSE":
        bpy.ops.object.mode_set(mode="POSE")


class SWISH_OT_armature_activate(bpy.types.Operator):
    bl_idname = "swish.armature_activate"
    bl_label = "Edit Armature"
    bl_description = "Make this armature the one the panels edit (and add groups to)"
    bl_options = {"REGISTER", "UNDO"}

    armature: bpy.props.StringProperty()

    def execute(self, context):
        obj = bpy.data.objects.get(self.armature)
        if obj is None or obj.type != "ARMATURE":
            return {"CANCELLED"}
        make_active(context, obj)
        return {"FINISHED"}


class SWISH_OT_group_new(bpy.types.Operator):
    bl_idname = "swish.group_new"
    bl_label = "New Group"
    bl_description = "Make a group of the chains under the bones selected in Pose Mode"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.object is not None and context.object.type == "ARMATURE"

    def execute(self, context):
        obj = context.object
        if context.mode != "POSE":
            bpy.ops.object.mode_set(mode="POSE")
        if not any(pb.id_data == obj for pb in context.selected_pose_bones or ()):
            self.report({"INFO"}, "Pose Mode: select the first bone of each chain, then click + again")
            return {"CANCELLED"}
        roots = _selected_roots(context)
        _claim(obj, roots)
        group = obj.swish.groups.add()
        group.name = group_name(obj, roots)
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
        group_curves.remove_owned(obj.swish.groups[obj.swish.active_group])
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
        wm = context.window_manager
        wm.progress_begin(scene.frame_start, scene.frame_end)
        try:
            live.bake_cache(scene, progress=wm.progress_update)
        finally:
            wm.progress_end()
        return {"FINISHED"}


class SWISH_OT_cache_toggle(bpy.types.Operator):
    bl_idname = "swish.cache_toggle"
    bl_label = "Cache"
    bl_description = ("Bake the whole frame range, to scrub and render; click again to clear it and play "
                      "live. A change to the setup clears the bake too")

    def execute(self, context):
        scene = context.scene
        if live.is_cached(scene):
            scene.swish.use_cache = False
            live.invalidate(scene)
            return {"FINISHED"}
        if not scene.swish.simulate:
            scene.swish.simulate = True
        scene.swish.use_cache = True
        wm = context.window_manager
        wm.progress_begin(scene.frame_start, scene.frame_end)
        try:
            live.bake_cache(scene, progress=wm.progress_update)
        finally:
            wm.progress_end()
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


def _selected_names(context):
    return [pb.name for pb in context.selected_pose_bones or () if pb.id_data == context.object]


def _active_item(collection, index):
    return collection[min(index, len(collection) - 1)] if len(collection) else None


class SWISH_OT_force_add(_GroupOperator, bpy.types.Operator):
    bl_idname = "swish.force_add"
    bl_label = "Add Force"
    bl_description = "Add an external force to the active group"
    bl_options = {"REGISTER", "UNDO"}

    kind: bpy.props.EnumProperty(name="Type", items=[item[:3] for item in FORCE_KINDS])

    def execute(self, context):
        group = _active_group(context)
        force = group.forces.add()
        force.name = next(label for key, label, *_ in FORCE_KINDS if key == self.kind)
        force.kind = self.kind
        if self.kind == "CURVE":
            for channel in FORCE_CHANNELS:
                group_curves.node(force, channel)
        if self.kind == "PROCEDURAL_WIND":
            # Blowing from a character's front to its back (Blender characters face -Y), as Kawaii's
            # Breeze, rather than Kawaii's all-zero defaults, which push nothing.
            force.direction = (0.0, 1.0, 0.0)
            presets.apply_wind(force, "BREEZE", 100.0 * context.scene.unit_settings.scale_length)
        group.active_force = len(group.forces) - 1
        live.invalidate(context.scene)
        return {"FINISHED"}


class SWISH_OT_wind_preset(_GroupOperator, bpy.types.Operator):
    bl_idname = "swish.wind_preset"
    bl_label = "Wind Preset"
    bl_description = "Set the Procedural Wind force to one of Kawaii's presets"
    bl_options = {"REGISTER", "UNDO"}

    preset: bpy.props.EnumProperty(name="Preset", items=presets.WIND_ITEMS)

    def execute(self, context):
        group = _active_group(context)
        force = _active_item(group.forces, group.active_force)
        if force is None or force.kind != "PROCEDURAL_WIND":
            return {"CANCELLED"}
        presets.apply_wind(force, self.preset, 100.0 * context.scene.unit_settings.scale_length)
        live.invalidate(context.scene)
        return {"FINISHED"}


class SWISH_OT_wind_field_add(bpy.types.Operator):
    bl_idname = "swish.wind_field_add"
    bl_label = "Add Wind Field"
    bl_description = "Add a Wind force field: it blows along its Z axis at its Strength"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        # Blender makes the new field the active object; the armature stays the one being edited.
        view_layer = context.view_layer
        armature = view_layer.objects.active
        mode = context.mode
        if mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.effector_add(type="WIND", rotation=(-1.5707963, 0.0, 0.0))
        field = view_layer.objects.active
        field.select_set(False)
        if armature is not None:
            view_layer.objects.active = armature
            armature.select_set(True)
            if mode == "POSE":
                bpy.ops.object.mode_set(mode="POSE")
        live.invalidate(context.scene)
        self.report({"INFO"}, f"Added '{field.name}': it blows along its Z axis at its Strength")
        return {"FINISHED"}


class SWISH_OT_force_remove(_GroupOperator, bpy.types.Operator):
    bl_idname = "swish.force_remove"
    bl_label = "Remove Force"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        group = _active_group(context)
        return group is not None and len(group.forces) > 0

    def execute(self, context):
        group = _active_group(context)
        index = min(group.active_force, len(group.forces) - 1)
        group_curves.remove(group.forces[index], ("rate",) + FORCE_CHANNELS)
        group.forces.remove(index)
        group.active_force = max(0, index - 1)
        live.invalidate(context.scene)
        return {"FINISHED"}


class SWISH_OT_force_filter(_GroupOperator, bpy.types.Operator):
    bl_idname = "swish.force_filter"
    bl_label = "Set Bone Filter"
    bl_description = "Set the force's bone filter to the selected bones, or clear it"
    bl_options = {"REGISTER", "UNDO"}

    target: bpy.props.EnumProperty(items=[("APPLY", "Only", ""), ("IGNORE", "Ignore", "")])
    clear: bpy.props.BoolProperty()

    def execute(self, context):
        group = _active_group(context)
        force = _active_item(group.forces, group.active_force)
        if force is None:
            return {"CANCELLED"}
        collection = force.apply_bones if self.target == "APPLY" else force.ignore_bones
        collection.clear()
        if not self.clear:
            names = _selected_names(context)
            if not names:
                self.report({"WARNING"}, "Select bones in Pose Mode")
                return {"CANCELLED"}
            for name in names:
                collection.add().name = name
        live.invalidate(context.scene)
        return {"FINISHED"}


class SWISH_OT_sync_add(_GroupOperator, bpy.types.Operator):
    bl_idname = "swish.sync_add"
    bl_label = "Add Sync Bone"
    bl_description = ("Add a sync bone to the active group, following the active bone; selected bones of the "
                      "group become its targets")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj, group = context.object, _active_group(context)
        sync = group.sync_bones.add()
        active = context.active_pose_bone if context.mode == "POSE" else None
        if active is not None:
            sync.bone = active.name
            sync.name = active.name
        from .selection import group_index_of_bone
        index = list(obj.swish.groups).index(group) if group in list(obj.swish.groups) else -1
        for name in _selected_names(context):
            if active is not None and name == active.name:
                continue
            if group_index_of_bone(obj, name) == index:
                sync.targets.add().bone = name
        group.active_sync = len(group.sync_bones) - 1
        live.invalidate(context.scene)
        return {"FINISHED"}


class SWISH_OT_sync_remove(_GroupOperator, bpy.types.Operator):
    bl_idname = "swish.sync_remove"
    bl_label = "Remove Sync Bone"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        group = _active_group(context)
        return group is not None and len(group.sync_bones) > 0

    def execute(self, context):
        group = _active_group(context)
        index = min(group.active_sync, len(group.sync_bones) - 1)
        sync = group.sync_bones[index]
        for target in sync.targets:
            group_curves.remove(target, ("rate",))
        group_curves.remove(sync, ("distance",))
        group.sync_bones.remove(index)
        group.active_sync = max(0, index - 1)
        live.invalidate(context.scene)
        return {"FINISHED"}


class SWISH_OT_sync_target_add(_PoseBonesOperator, bpy.types.Operator):
    bl_idname = "swish.sync_target_add"
    bl_label = "Add Sync Targets"
    bl_description = "Add the selected bones as targets of the active sync bone"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        group = _active_group(context)
        return super().poll(context) and group is not None and len(group.sync_bones) > 0

    def execute(self, context):
        group = _active_group(context)
        sync = _active_item(group.sync_bones, group.active_sync)
        existing = {target.bone for target in sync.targets}
        for name in _selected_names(context):
            if name != sync.bone and name not in existing:
                sync.targets.add().bone = name
        live.invalidate(context.scene)
        return {"FINISHED"}


class SWISH_OT_sync_target_remove(_GroupOperator, bpy.types.Operator):
    bl_idname = "swish.sync_target_remove"
    bl_label = "Remove Sync Target"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        group = _active_group(context)
        sync = _active_item(group.sync_bones, group.active_sync)
        if sync is None or not len(sync.targets):
            return {"CANCELLED"}
        index = min(sync.active_target, len(sync.targets) - 1)
        group_curves.remove(sync.targets[index], ("rate",))
        sync.targets.remove(index)
        sync.active_target = max(0, index - 1)
        live.invalidate(context.scene)
        return {"FINISHED"}


def _chain_bones(obj, group, root):
    return chain_links.chain_subtree(obj, root, [bone.name for bone in group.excluded])


def selected_chains(obj, group):
    """The group's chains with a bone selected: picked in the Chains list or in the viewport
    (click, Shift-click, box select). Returned as root names, in the group's order."""
    selected = {pb.name for pb in obj.pose.bones if pb.select}
    if not selected:
        return []
    return [root.name for root in group.roots if selected & set(_chain_bones(obj, group, root.name))]



def _move_chains(obj, source, roots, target):
    """Chains from one group to another: their roots, exclusions and the links inside them.
    A link from a moved chain to one left behind cannot follow and is removed; returns how many."""
    moved, whole = set(), set()
    for root in roots:
        moved |= set(_chain_bones(obj, source, root))
        whole |= set(chain_links.chain_subtree(obj, root))        # with its excluded bones
    if target is not None:
        for root in source.roots:                        # in the source's order
            if root.name in roots:
                target.roots.add().name = root.name
    for index in reversed(range(len(source.roots))):
        if source.roots[index].name in roots:
            source.roots.remove(index)
    for index in reversed(range(len(source.excluded))):
        name = source.excluded[index].name
        if name in whole:
            if target is not None:
                target.excluded.add().name = name
            source.excluded.remove(index)
    broken = 0
    for index in reversed(range(len(source.links))):
        link = source.links[index]
        inside = (link.bone_a in moved, link.bone_b in moved)
        if inside == (True, True) and target is not None:
            copy = target.links.add()
            copy.bone_a, copy.bone_b = link.bone_a, link.bone_b
            copy.compliance, copy.exclude_from_subdivision = link.compliance, link.exclude_from_subdivision
            source.links.remove(index)
        elif any(inside):
            source.links.remove(index)
            broken += 1
    return broken


def _drop_if_empty(obj, group):
    """A group whose last chain left is removed with it."""
    if len(group.roots):
        return False
    index = list(obj.swish.groups).index(group)
    group_curves.remove_owned(group)
    obj.swish.groups.remove(index)
    obj.swish.active_group = max(0, min(obj.swish.active_group, len(obj.swish.groups) - 1))
    return True


def all_chains(obj):
    """Every chain of the armature as (group index, root), in the Chains list's order."""
    return [(index, root.name) for index, group in enumerate(obj.swish.groups) for root in group.roots]


def _bones_of(obj, chains):
    found = set()
    for index, root in chains:
        found |= set(_chain_bones(obj, obj.swish.groups[index], root))
    return found


def selected_chain_keys(obj):
    """(group index, root) of every chain with a selected bone."""
    selected = {pb.name for pb in obj.pose.bones if pb.select}
    if not selected:
        return set()
    return {(index, root) for index, root in all_chains(obj)
            if selected & set(_chain_bones(obj, obj.swish.groups[index], root))}


def _select(context, obj, chains, keep=False, deselect=()):
    """Select chains' bones in the viewport (Pose Mode), as clicking their rows does."""
    if context.mode != "POSE" and context.view_layer.objects.active == obj:
        bpy.ops.object.mode_set(mode="POSE")
    wanted, dropped = _bones_of(obj, chains), _bones_of(obj, deselect)
    for pb in obj.pose.bones:
        if pb.name in wanted:
            pb.select = True
        elif pb.name in dropped or not keep:
            pb.select = False


_last_clicked = {}                   # armature -> the chain clicked last, for Shift-click ranges


class SWISH_OT_chain_click(bpy.types.Operator):
    bl_idname = "swish.chain_click"
    bl_label = "Select Chain"
    bl_description = "Select this chain. Shift-click selects a range; Ctrl-click adds or drops one chain"
    bl_options = {"REGISTER", "UNDO"}

    group: bpy.props.IntProperty()
    root: bpy.props.StringProperty()
    extend: bpy.props.BoolProperty(options={"SKIP_SAVE"})
    span: bpy.props.BoolProperty(options={"SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        return context.object is not None and context.object.type == "ARMATURE"

    def invoke(self, context, event):
        # As in a file browser: Shift selects a range, Ctrl adds or drops one.
        self.span, self.extend = event.shift, event.ctrl
        return self.execute(context)

    def execute(self, context):
        obj = context.object
        chains = all_chains(obj)
        this = (self.group, self.root)
        if this not in chains:
            return {"CANCELLED"}
        last = _last_clicked.get(obj.name)
        if self.span and last in chains:
            a, b = sorted((chains.index(last), chains.index(this)))
            _select(context, obj, chains[a:b + 1], keep=True)
        elif self.extend:
            if this in selected_chain_keys(obj):
                _select(context, obj, [], keep=True, deselect=[this])
            else:
                _select(context, obj, [this], keep=True)
        else:
            _select(context, obj, [this])
        _last_clicked[obj.name] = this
        obj.swish.active_group = self.group
        return {"FINISHED"}


class SWISH_OT_chains_select(bpy.types.Operator):
    bl_idname = "swish.chains_select"
    bl_label = "Select Chains"
    bl_description = "Select every chain of the armature, or none"
    bl_options = {"REGISTER", "UNDO"}

    action: bpy.props.EnumProperty(items=(("ALL", "All", ""), ("NONE", "None", ""), ("TOGGLE", "Toggle", "")),
                                   default="TOGGLE")

    @classmethod
    def poll(cls, context):
        return context.object is not None and context.object.type == "ARMATURE"

    def execute(self, context):
        obj = context.object
        chains = all_chains(obj)
        every = self.action == "ALL" or (self.action == "TOGGLE" and len(selected_chain_keys(obj)) < len(chains))
        _select(context, obj, chains if every else [], deselect=() if every else chains)
        return {"FINISHED"}


class SWISH_OT_group_click(bpy.types.Operator):
    bl_idname = "swish.group_click"
    bl_label = "Select Group"
    bl_description = "Edit this group and select its chains. Ctrl or Shift adds them to the selection"
    bl_options = {"REGISTER", "UNDO"}

    index: bpy.props.IntProperty()
    extend: bpy.props.BoolProperty(options={"SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        return context.object is not None and context.object.type == "ARMATURE"

    def execute(self, context):
        obj = context.object
        if not 0 <= self.index < len(obj.swish.groups):
            return {"CANCELLED"}
        mine = [(self.index, root.name) for root in obj.swish.groups[self.index].roots]
        if self.extend and mine and set(mine) <= selected_chain_keys(obj):
            _select(context, obj, [], keep=True, deselect=mine)        # all selected already: drop them
        else:
            _select(context, obj, mine, keep=self.extend)
        if mine:
            _last_clicked[obj.name] = mine[-1]
        obj.swish.active_group = self.index
        return {"FINISHED"}


def _remove_chains(obj, chains):
    """Stop simulating chains, dropping groups they empty. Returns the links removed."""
    broken, names = 0, [g.name for g in obj.swish.groups]
    by_group = {}
    for index, root in chains:
        by_group.setdefault(names[index], []).append(root)
    for name, roots in by_group.items():
        group = obj.swish.groups[[g.name for g in obj.swish.groups].index(name)]
        broken += _move_chains(obj, group, roots, None)
        _drop_if_empty(obj, group)
    return broken


class SWISH_OT_chain_remove(bpy.types.Operator):
    bl_idname = "swish.chain_remove"
    bl_label = "Remove Chain"
    bl_description = "Stop simulating this chain (a group left empty is removed)"
    bl_options = {"REGISTER", "UNDO"}

    group: bpy.props.IntProperty()
    root: bpy.props.StringProperty()

    def execute(self, context):
        obj = context.object
        if obj is None or (self.group, self.root) not in all_chains(obj):
            return {"CANCELLED"}
        _remove_chains(obj, [(self.group, self.root)])
        live.mark_dirty(context.scene)
        return {"FINISHED"}


class SWISH_OT_chains_remove(bpy.types.Operator):
    bl_idname = "swish.chains_remove"
    bl_label = "Remove Selected Chains"
    bl_description = "Stop simulating the selected chains"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = context.object
        chains = sorted(selected_chain_keys(obj)) if obj is not None and obj.type == "ARMATURE" else []
        if not chains:
            self.report({"WARNING"}, "Select chains first: click their rows, or their bones in the viewport")
            return {"CANCELLED"}
        broken = _remove_chains(obj, chains)
        live.mark_dirty(context.scene)
        self.report({"INFO"}, f"Removed {len(chains)} chains" + (f" and {broken} links" if broken else ""))
        return {"FINISHED"}


def merge_groups(obj, sources, target):
    """Move every chain of the source groups into the target, which keeps its settings, and remove the
    sources. Returns the target's index afterwards."""
    name = target.name
    for source in sources:
        if source == target:
            continue
        _move_chains(obj, source, [root.name for root in source.roots], target)
        _drop_if_empty(obj, source)
    index = [g.name for g in obj.swish.groups].index(name)
    obj.swish.active_group = index
    return index


class SWISH_OT_groups_merge(bpy.types.Operator):
    bl_idname = "swish.groups_merge"
    bl_label = "Merge Groups"
    bl_description = ("Merge the groups of the selected chains into one: the active group if it is among them, "
                      "keeping its settings")
    bl_options = {"REGISTER", "UNDO"}

    source: bpy.props.IntProperty(default=-1, options={"SKIP_SAVE"})
    target: bpy.props.IntProperty(default=-1, options={"SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        return context.object is not None and context.object.type == "ARMATURE"

    def execute(self, context):
        obj = context.object
        groups = obj.swish.groups
        if self.source >= 0 and self.target >= 0:
            if self.source == self.target or max(self.source, self.target) >= len(groups):
                return {"CANCELLED"}
            chosen, target = [groups[self.source]], groups[self.target]
        else:
            indices = sorted({index for index, _root in selected_chain_keys(obj)})
            if len(indices) < 2:
                self.report({"WARNING"}, "Select chains from two or more groups")
                return {"CANCELLED"}
            chosen = [groups[i] for i in indices]
            target = groups[obj.swish.active_group] if obj.swish.active_group in indices else chosen[0]
        names = [g.name for g in chosen if g != target]
        merge_groups(obj, [g for g in chosen if g != target], target)
        live.mark_dirty(context.scene)
        self.report({"INFO"}, f"Merged {', '.join(names)} into '{target.name}'")
        return {"FINISHED"}


class SWISH_OT_group_pick(bpy.types.Operator):
    bl_idname = "swish.group_pick"
    bl_label = "Edit Group"
    bl_description = "Edit this group's settings"

    index: bpy.props.IntProperty()

    def execute(self, context):
        obj = context.object
        if obj is None or not 0 <= self.index < len(obj.swish.groups):
            return {"CANCELLED"}
        obj.swish.active_group = self.index
        return {"FINISHED"}


def _chains_everywhere(obj):
    """(group, [roots]) for every group of the armature with chains holding selected bones."""
    return [(group, roots) for group in obj.swish.groups for roots in [selected_chains(obj, group)] if roots]


class SWISH_OT_chains_to_group(bpy.types.Operator):
    bl_idname = "swish.chains_to_group"
    bl_label = "Move Chains to Group"
    bl_description = "Move the chains holding the selected bones, from whatever groups, into this group"
    bl_options = {"REGISTER", "UNDO"}

    index: bpy.props.IntProperty(default=-1, description="The group; -1 makes a new one")

    @classmethod
    def poll(cls, context):
        obj = context.object
        return obj is not None and obj.type == "ARMATURE"

    def execute(self, context):
        obj = context.object
        found = _chains_everywhere(obj)
        if not found:
            self.report({"WARNING"}, "Select bones of the chains to move")
            return {"CANCELLED"}
        if self.index < 0:
            target = obj.swish.groups.add()
            target.name = group_name(obj, [root for _group, roots in found for root in roots])
            serialize.paste(target, serialize.settings_text(found[0][0]))
        else:
            target = obj.swish.groups[self.index]
        name, moved, broken = target.name, 0, 0
        for group, roots in found:
            if group == target:
                continue
            broken += _move_chains(obj, group, roots, target)
            moved += len(roots)
        for group, _roots in found:
            if group != target and group.name in [g.name for g in obj.swish.groups]:
                _drop_if_empty(obj, group)
        obj.swish.active_group = [g.name for g in obj.swish.groups].index(name)
        live.mark_dirty(context.scene)
        self.report({"INFO"}, f"{moved} chains to '{name}'" + (f"; {broken} links removed" if broken else ""))
        return {"FINISHED"}


class SWISH_MT_chains_to_group(bpy.types.Menu):
    bl_idname = "SWISH_MT_chains_to_group"
    bl_label = "Move Chains to Group"

    def draw(self, context):
        layout = self.layout
        obj = context.object
        for index, group in enumerate(obj.swish.groups if obj is not None and obj.type == "ARMATURE" else ()):
            layout.operator("swish.chains_to_group", text=group.name, icon="BONE_DATA").index = index
        layout.separator()
        layout.operator("swish.chains_to_group", text="New Group", icon="ADD").index = -1


def _pose_menu(self, context):
    obj = context.object
    if obj is not None and obj.type == "ARMATURE" and len(obj.swish.groups):
        self.layout.separator()
        self.layout.menu("SWISH_MT_chains_to_group", icon="PHYSICS")


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
           SWISH_OT_setup_export, SWISH_OT_setup_import, SWISH_OT_force_add, SWISH_OT_force_remove,
           SWISH_OT_force_filter, SWISH_OT_sync_add, SWISH_OT_sync_remove, SWISH_OT_sync_target_add,
           SWISH_OT_sync_target_remove, SWISH_OT_wind_preset, SWISH_OT_wind_field_add, SWISH_OT_chain_click,
           SWISH_OT_chains_select, SWISH_OT_group_click, SWISH_OT_groups_merge, SWISH_OT_chains_remove, SWISH_OT_cache_toggle, SWISH_OT_armature_activate,
           SWISH_OT_chains_to_group, SWISH_MT_chains_to_group, SWISH_OT_chain_remove, SWISH_OT_group_pick)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.VIEW3D_MT_pose_context_menu.append(_pose_menu)


def unregister():
    bpy.types.VIEW3D_MT_pose_context_menu.remove(_pose_menu)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
