"""Where group curves live: Float Curve nodes in one hidden node group.

Blender lets add-ons draw a curve widget only for a curve that already exists
in some datablock (Swingy Bone Physics borrows brushes). Brushes are assets in
Blender 5 and would crowd the brush shelves, so Swish keeps one node group,
".Swish Curves", with a Float Curve node per group and setting. The leading
dot keeps it out of the node editor's lists; a fake user keeps it saved.
"""
import uuid

import bpy

from ..solver.curves import LinearCurve

HOST = ".Swish Curves"
CURVED = ("damping", "stiffness", "world_damping_location", "world_damping_rotation", "radius", "limit_angle")

_cache = {}                  # node name -> (points signature, LinearCurve)


def host(create=True):
    tree = bpy.data.node_groups.get(HOST)
    if tree is None and create:
        tree = bpy.data.node_groups.new(HOST, "ShaderNodeTree")
        tree.use_fake_user = True
    return tree


def key(group):
    """The group's stable id, naming its curve nodes."""
    if not group.curve_key:
        group.curve_key = uuid.uuid4().hex[:12]
    return group.curve_key


def node(group, setting, create=True):
    """The Float Curve node holding one setting's curve for a group."""
    tree = host(create)
    if tree is None:
        return None
    name = f"{key(group) if create else group.curve_key}.{setting}"
    found = tree.nodes.get(name)
    if found is None and create:
        found = tree.nodes.new("ShaderNodeFloatCurve")
        found.name = name
        found.label = name
        mapping = found.mapping
        # Curves multiply their setting: 0 to 2 along the chain, drawn with 1 mid-height
        # so the default flat curve is visible and easy to grab.
        mapping.use_clip = True
        mapping.clip_min_x, mapping.clip_max_x = 0.0, 1.0
        mapping.clip_min_y, mapping.clip_max_y = 0.0, 2.0
        points = mapping.curves[0].points
        points[0].location = (0.0, 1.0)
        points[1].location = (1.0, 1.0)
        mapping.reset_view()
        mapping.update()
    return found


def curve(group, setting):
    """The setting's curve as linear keys, or None when the group does not use one."""
    if not getattr(group, f"use_{setting}_curve"):
        return None
    found = node(group, setting, create=False)
    if found is None:
        return None
    mapping = found.mapping
    points = mapping.curves[0].points
    signature = tuple((tuple(p.location), p.handle_type) for p in points)
    cached = _cache.get(found.name)
    if cached is not None and cached[0] == signature:
        return cached[1]
    mapping.initialize()
    sampled = LinearCurve.sampled(lambda x: mapping.evaluate(mapping.curves[0], x))
    _cache[found.name] = (signature, sampled)
    return sampled


def curves(group):
    return {setting: curve(group, setting) for setting in CURVED}


def remove(group):
    tree = host(create=False)
    if tree is None or not group.curve_key:
        return
    for setting in CURVED:
        found = tree.nodes.get(f"{group.curve_key}.{setting}")
        if found is not None:
            tree.nodes.remove(found)
