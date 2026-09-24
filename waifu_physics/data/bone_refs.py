"""The bones an armature's groups name, kept right when bones are renamed.

Groups hold bones by name (chain roots, exclusions, links, force filters, sync bones), and Blender updates
none of those when a bone is renamed. So each armature remembers where every bone its groups name sits at
rest (head, tail, parent). When a name goes missing, the reference moves to the one bone now standing in
that place under that parent; VRoid stacks duplicate chains at identical positions, so the parent breaks
ties, and a bone that cannot be told apart is left for the user (missing(), clean_up())."""
import math

_TOLERANCE = 1.0e-5


def slots(obj):
    """(owner, attribute) of every bone name the armature's groups hold."""
    for group in obj.waifu_physics.groups:
        for item in group.roots:
            yield item, "name"
        for item in group.excluded:
            yield item, "name"
        for link in group.links:
            yield link, "bone_a"
            yield link, "bone_b"
        for force in group.forces:
            for item in force.apply_bones:
                yield item, "name"
            for item in force.ignore_bones:
                yield item, "name"
        for sync in group.sync_bones:
            yield sync, "bone"
            for target in sync.targets:
                yield target, "bone"


def named(obj):
    return {getattr(owner, attribute) for owner, attribute in slots(obj)} - {""}


def _rest(bone):
    return tuple(bone.head_local) + tuple(bone.tail_local)


def _same_place(a, b, scale):
    return all(math.isclose(x, y, rel_tol=0.0, abs_tol=_TOLERANCE * scale) for x, y in zip(a, b))


def remember(obj):
    """Record where the named bones are. Writes only when something changed (a write is an update)."""
    bones = obj.data.bones
    wanted = {}
    for name in sorted(named(obj)):
        bone = bones.get(name)
        if bone is not None:
            wanted[name] = (bone.parent.name if bone.parent else "", _rest(bone))
    known = obj.waifu_physics.known_bones
    kept = {item.name: (item.parent, tuple(item.rest)) for item in known}
    for name, value in list(kept.items()):
        if name not in wanted and name in named(obj):
            wanted[name] = value                 # missing now: keep what is known, to find it later
    if kept.keys() == wanted.keys() and all(kept[n][0] == wanted[n][0] and _same_place(kept[n][1], wanted[n][1], 1.0)
                                            for n in wanted):
        return False
    known.clear()
    for name, (parent, rest) in wanted.items():
        item = known.add()
        item.name, item.parent, item.rest = name, parent, rest
    return True


def missing(obj):
    """Names the groups hold that the armature has no bone for, sorted."""
    if obj.type != "ARMATURE" or obj.data is None:
        return []
    bones = obj.data.bones
    return sorted(name for name in named(obj) if bones.get(name) is None)


def repair(obj):
    """Follow renamed bones. Returns {old name: new name} of what was followed."""
    if obj.type != "ARMATURE" or obj.data is None or not len(obj.waifu_physics.groups):
        return {}
    lost = missing(obj)
    renamed = {}
    if lost:
        bones = obj.data.bones
        known = {item.name: (item.parent, tuple(item.rest)) for item in obj.waifu_physics.known_bones}
        scale = max((max(abs(v) for v in _rest(b)) for b in bones), default=1.0) or 1.0
        taken = named(obj)
        for name in lost:
            if name not in known:
                continue
            parent, rest = known[name]
            found = [b for b in bones if b.name not in taken and _same_place(_rest(b), rest, scale)]
            if len(found) > 1:                   # stacked duplicates: the parent decides
                found = [b for b in found
                         if (b.parent.name if b.parent else "") == renamed.get(parent, parent)]
            if len(found) == 1:
                renamed[name] = found[0].name
                taken.add(found[0].name)
        for owner, attribute in slots(obj):
            new = renamed.get(getattr(owner, attribute))
            if new is not None:
                setattr(owner, attribute, new)
    remember(obj)
    return renamed


def clean_up(obj):
    """Drop what names a bone the armature no longer has. Returns how many references went."""
    gone = set(missing(obj))
    if not gone:
        return 0
    count = 0

    def prune(collection, attribute="name"):
        nonlocal count
        for index in reversed(range(len(collection))):
            if getattr(collection[index], attribute) in gone:
                collection.remove(index)
                count += 1

    for group in obj.waifu_physics.groups:
        prune(group.roots)
        prune(group.excluded)
        for index in reversed(range(len(group.links))):
            link = group.links[index]
            if link.bone_a in gone or link.bone_b in gone:
                group.links.remove(index)
                count += 1
        for force in group.forces:
            prune(force.apply_bones)
            prune(force.ignore_bones)
        prune(group.sync_bones, "bone")
        for sync in group.sync_bones:
            prune(sync.targets, "bone")
    remember(obj)
    return count
