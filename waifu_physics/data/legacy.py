"""Files saved while this add-on was called Swish Physics: on load, their groups, scene settings and
colliders move to the new names, as do the datablocks the add-on made (its curves node group, the collider
node tree, modifiers and collection)."""
import bpy

from . import colliders, curves

# (collection of IDs, old property, new one): what the add-on's PointerProperties kept under the old names.
# Blender 5 keeps registered properties apart from custom ones, in the ID's system properties.
_PROPERTIES = (("objects", "swish", "waifu_physics"), ("objects", "swish_collider", "waifu_physics_collider"),
               ("scenes", "swish", "waifu_physics"))
_BLOCKS = (("node_groups", ".Swish Curves", curves.HOST), ("node_groups", ".Swish Collider", colliders.TREE),
           ("collections", "Swish Colliders", colliders.COLLECTION))
_MODIFIER = "Swish Collider"


def migrate():
    """Move old-named data to the new names. Returns how many things moved."""
    moved = 0
    for kind, old, new in _PROPERTIES:
        for block in getattr(bpy.data, kind):
            stored = block.bl_system_properties_get() if block.library is None else None
            if stored is None or old not in stored:
                continue
            if not stored.get(new):                  # nothing there yet (or only the empty group Blender made)
                if new in stored:
                    del stored[new]                  # a group cannot be assigned over, only replaced
                stored[new] = stored[old].to_dict()
                moved += 1
            del stored[old]
    for kind, old, new in _BLOCKS:
        found = getattr(bpy.data, kind).get(old)
        if found is not None and found.library is None and getattr(bpy.data, kind).get(new) is None:
            found.name = new
            moved += 1
    for obj in bpy.data.objects:
        if obj.library is None and obj.type == "ARMATURE":
            for group in obj.waifu_physics.groups:
                moved += upgrade_group(group)
    for obj in bpy.data.objects:
        if obj.library is None and obj.type == "MESH":
            modifier = obj.modifiers.get(_MODIFIER)
            if modifier is not None and obj.modifiers.get(colliders.MODIFIER) is None:
                modifier.name = colliders.MODIFIER
                moved += 1
    return moved


def _stored(group, name):
    """Has the group ever stored this setting (a value read back is otherwise only the default)?"""
    try:
        return name in group.bl_system_properties_get()
    except (AttributeError, TypeError):
        return True


def upgrade_group(group):
    """Kawaii's Simple External Force and scene wind, from before every force lived in the Forces list: the
    simple force becomes a Push force (it adds the same push each step), and scene wind or Kawaii's Wind force
    turns on Blender Force Fields, which reads the same Wind fields as Blender does. Returns whether it changed."""
    from . import curves
    changed = False
    # Collide Against Every Collider arrived on by default: a group whose collider list was edited before keeps its list.
    if not _stored(group, "use_all_colliders") and (group.custom_collider_sets or len(group.collider_sets)):
        group.use_all_colliders = False
        changed = True
    simple = tuple(group.simple_external_force)
    if any(value != 0.0 for value in simple):
        force = group.forces.add()
        force.kind, force.name = "BASIC", "Push"
        force.direction = simple
        force.space = "WORLD" if group.world_space_simple_external_force else "COMPONENT"
        group.simple_external_force = (0.0, 0.0, 0.0)
        changed = True
    if group.enable_wind:
        group.use_force_fields, group.force_field_strength = True, group.wind_scale
        group.enable_wind = False
        changed = True
    for index in reversed(range(len(group.forces))):
        if group.forces[index].kind == "WIND":
            group.use_force_fields = True
            curves.remove(group.forces[index], ("rate",))
            group.forces.remove(index)
            changed = True
    if changed:
        group.active_force = max(0, min(group.active_force, len(group.forces) - 1))
    return changed


@bpy.app.handlers.persistent
def _file_loaded(_dummy):
    migrate()


def _first_run():
    try:
        migrate()                   # the file open when the add-on was enabled
    except AttributeError:
        return 0.5                  # bpy.data not ready yet
    return None


def register():
    bpy.app.handlers.load_post.append(_file_loaded)
    bpy.app.timers.register(_first_run, first_interval=0.0)


def unregister():
    if _file_loaded in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_file_loaded)
    if bpy.app.timers.is_registered(_first_run):
        bpy.app.timers.unregister(_first_run)
