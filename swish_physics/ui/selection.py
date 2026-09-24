"""Groups from the bones selected in Pose Mode.

Follow Selection shows the group of whichever bone was just made active. There
is no selection-changed event in Blender, so a light timer checks the active
bone a few times a second and acts only when it changes -- picking a group in
the list still sticks until another bone is clicked.
"""
import bpy

_last_active = {}            # object name -> the active bone we last followed


def group_index_of_bone(obj, name):
    """The group whose chains hold this bone, or -1."""
    bone = obj.pose.bones.get(name)
    if bone is None:
        return -1
    path = []
    while bone is not None:
        path.append(bone.name)
        bone = bone.parent
    for index, group in enumerate(obj.swish.groups):
        excluded = {b.name for b in group.excluded}
        roots = {r.name for r in group.roots}
        for depth, bone_name in enumerate(path):
            if bone_name in excluded:
                break
            if bone_name in roots:
                return index
    return -1


def groups_of_selected(context):
    """Every group holding a selected bone, across the armatures in Pose Mode."""
    found = []
    for pose_bone in context.selected_pose_bones or ():
        obj = pose_bone.id_data
        index = group_index_of_bone(obj, pose_bone.name)
        if index >= 0:
            group = obj.swish.groups[index]
            if all(group != g for g in found):
                found.append(group)
    return found


def _follow():
    try:
        context = bpy.context
        obj = context.object
        if (obj is not None and obj.type == "ARMATURE" and obj.mode == "POSE"
                and context.scene is not None and context.scene.swish.follow_selection):
            active = obj.data.bones.active
            name = active.name if active is not None else ""
            if name and _last_active.get(obj.name) != name:
                _last_active[obj.name] = name
                index = group_index_of_bone(obj, name)
                if index >= 0 and obj.swish.active_group != index:
                    obj.swish.active_group = index
    except (AttributeError, ReferenceError):
        pass
    return 0.25


def register():
    if not bpy.app.timers.is_registered(_follow):
        bpy.app.timers.register(_follow, first_interval=0.25, persistent=True)


def unregister():
    if bpy.app.timers.is_registered(_follow):
        bpy.app.timers.unregister(_follow)
    _last_active.clear()
