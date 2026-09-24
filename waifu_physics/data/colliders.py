"""Colliders: mesh objects parented to bones, their shape drawn by Geometry Nodes.

A collider belongs to the armature it is parented to; a group collides with
the colliders of its own armature, or of the armatures listed in its collider
sets. Its shape and sizes are the inputs of its "Waifu Physics Collider" modifier
(animatable), and its object scale multiplies them:

    Sphere, Inner Sphere   radius  x the largest axis scale
    Capsule                radius  x the larger of the X and Z scales,
                           length  x the Y scale (capsules run along local Y, as bones do)
    Tapered Capsule        as the capsule; Radius at the +Y end, Radius 1 at -Y
    Box                    half extents x the X, Y and Z scales
    Plane                  the local XY plane, facing +Z (size is only for drawing)

The node group draws exactly that shape: it builds it at the effective sizes
and divides out the object's own scale, so what is drawn is what collides.
Kawaii's capsules run along Z; `shape_of` turns ours onto its axis.
"""
import math

import bpy
import numpy as np
from mathutils import Matrix, Quaternion, Vector

from ..solver.system import Shape, SPHERE_OUTER, SPHERE_INNER, CAPSULE, TAPERED, BOX, PLANE

TREE = ".Waifu Physics Collider"
MODIFIER = "Waifu Physics Collider"
TREE_VERSION = 1
SHAPES = ("Sphere", "Inner Sphere", "Capsule", "Tapered Capsule", "Box", "Plane")
KINDS = {"Sphere": SPHERE_OUTER, "Inner Sphere": SPHERE_INNER, "Capsule": CAPSULE, "Tapered Capsule": TAPERED,
         "Box": BOX, "Plane": PLANE}
# Turns a shape running along local Y onto Kawaii's Z: X stays, Z goes to Y.
Y_TO_Z = Quaternion((1.0, 0.0, 0.0), -math.pi / 2)
COLLECTION = "Waifu Physics Colliders"


def node_group():
    """The collider node group, built once (and rebuilt if an older version is found)."""
    tree = bpy.data.node_groups.get(TREE)
    if tree is not None and tree.get("waifu_physics_version") == TREE_VERSION:
        return tree
    if tree is None:
        tree = bpy.data.node_groups.new(TREE, "GeometryNodeTree")
    tree.nodes.clear()
    tree.interface.clear()
    tree["waifu_physics_version"] = TREE_VERSION
    iface = tree.interface
    iface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    iface.new_socket("Shape", in_out="INPUT", socket_type="NodeSocketMenu")
    for name, default in (("Radius", 0.05), ("Radius 1", 0.05), ("Length", 0.2)):
        socket = iface.new_socket(name, in_out="INPUT", socket_type="NodeSocketFloat")
        socket.default_value = default
        socket.min_value = 0.0
        socket.subtype = "DISTANCE"
    extent = iface.new_socket("Extent", in_out="INPUT", socket_type="NodeSocketVector")
    extent.default_value = (0.05, 0.05, 0.05)
    extent.subtype = "TRANSLATION"

    nodes, links = tree.nodes, tree.links
    new = nodes.new
    gi, go = new("NodeGroupInput"), new("NodeGroupOutput")

    def math_node(op, a, b=None):
        node = new("ShaderNodeMath")
        node.operation = op
        links.new(a, node.inputs[0])
        if b is not None:
            if isinstance(b, (int, float)):
                node.inputs[1].default_value = b
            else:
                links.new(b, node.inputs[1])
        return node.outputs[0]

    def vector_node(op, a, b=None):
        node = new("ShaderNodeVectorMath")
        node.operation = op
        links.new(a, node.inputs[0])
        if b is not None:
            if isinstance(b, (tuple, list)):
                node.inputs[1].default_value = b
            else:
                links.new(b, node.inputs[1])
        return node.outputs[0] if op not in ("DOT_PRODUCT", "LENGTH") else node.outputs[1]

    # The object's own scale, which the drawing divides out.
    info = new("GeometryNodeObjectInfo")
    info.transform_space = "ORIGINAL"
    links.new(new("GeometryNodeSelfObject").outputs[0], info.inputs["Object"])
    scale = info.outputs["Scale"]
    split = new("ShaderNodeSeparateXYZ")
    links.new(scale, split.inputs[0])
    ax, ay, az = (math_node("ABSOLUTE", split.outputs[k]) for k in range(3))
    most = math_node("MAXIMUM", math_node("MAXIMUM", ax, ay), az)
    most_xz = math_node("MAXIMUM", ax, az)
    radius, radius1, length = gi.outputs["Radius"], gi.outputs["Radius 1"], gi.outputs["Length"]

    def sphere(r):
        uv = new("GeometryNodeMeshUVSphere")
        uv.inputs["Segments"].default_value = 24
        uv.inputs["Rings"].default_value = 11            # odd: no ring on the equator, so capsules stretch cleanly
        if isinstance(r, (int, float)):
            uv.inputs["Radius"].default_value = r
        else:
            links.new(r, uv.inputs["Radius"])
        return uv.outputs["Mesh"]

    def along_y(geometry):
        turn = new("GeometryNodeTransform")
        turn.inputs["Rotation"].default_value = (-math.pi / 2, 0.0, 0.0)
        links.new(geometry, turn.inputs["Geometry"])
        return turn.outputs["Geometry"]

    def z_sign():
        position = new("GeometryNodeInputPosition")
        parts = new("ShaderNodeSeparateXYZ")
        links.new(position.outputs[0], parts.inputs[0])
        return math_node("SIGN", parts.outputs[2]), position.outputs[0]

    half_length = math_node("MULTIPLY", math_node("MULTIPLY", length, ay), 0.5)

    # Sphere and inner sphere.
    ball = sphere(math_node("MULTIPLY", radius, most))
    # Capsule: a sphere of the capsule's radius, its halves pushed apart along Z, then laid along Y.
    capsule_sphere = sphere(math_node("MULTIPLY", radius, most_xz))
    sign, _position = z_sign()
    offset = new("ShaderNodeCombineXYZ")
    links.new(math_node("MULTIPLY", sign, half_length), offset.inputs[2])
    stretched = new("GeometryNodeSetPosition")
    links.new(capsule_sphere, stretched.inputs["Geometry"])
    links.new(offset.outputs[0], stretched.inputs["Offset"])
    capsule = along_y(stretched.outputs[0])
    # Tapered capsule: the +Z half at Radius, the -Z half at Radius 1.
    unit = sphere(1.0)
    sign_t, position_t = z_sign()
    top = math_node("GREATER_THAN", sign_t, 0.0)
    r_top = math_node("MULTIPLY", radius, most_xz)
    r_bottom = math_node("MULTIPLY", radius1, most_xz)
    chosen = new("GeometryNodeSwitch")
    chosen.input_type = "FLOAT"
    links.new(top, chosen.inputs["Switch"])
    links.new(r_bottom, chosen.inputs["False"])
    links.new(r_top, chosen.inputs["True"])
    scaled = vector_node("SCALE", position_t)
    links.new(chosen.outputs[0], scaled.node.inputs["Scale"])
    offset_t = new("ShaderNodeCombineXYZ")
    links.new(math_node("MULTIPLY", sign_t, half_length), offset_t.inputs[2])
    placed = vector_node("ADD", scaled, offset_t.outputs[0])
    tapered_set = new("GeometryNodeSetPosition")
    links.new(unit, tapered_set.inputs["Geometry"])
    links.new(placed, tapered_set.inputs["Position"])
    tapered = along_y(tapered_set.outputs[0])
    # Box: twice the half extents, times the scale.
    size = vector_node("MULTIPLY", vector_node("MULTIPLY", gi.outputs["Extent"], (2.0, 2.0, 2.0)),
                       vector_node("ABSOLUTE", scale))
    cube = new("GeometryNodeMeshCube")
    links.new(size, cube.inputs["Size"])
    # Plane: a square the size of four radii, with a line along its normal.
    grid = new("GeometryNodeMeshGrid")
    grid.inputs["Vertices X"].default_value = 2
    grid.inputs["Vertices Y"].default_value = 2
    side = math_node("MULTIPLY", math_node("MULTIPLY", radius, most), 4.0)
    links.new(side, grid.inputs["Size X"])
    links.new(side, grid.inputs["Size Y"])
    normal = new("GeometryNodeMeshLine")
    normal.mode = "END_POINTS"
    normal.inputs["Count"].default_value = 2
    tip = new("ShaderNodeCombineXYZ")
    links.new(math_node("MULTIPLY", side, 0.5), tip.inputs[2])
    links.new(tip.outputs[0], normal.inputs["Offset"])          # shown as End Location in this mode
    plane = new("GeometryNodeJoinGeometry")
    links.new(normal.outputs[0], plane.inputs[0])
    links.new(grid.outputs["Mesh"], plane.inputs[0])

    menu = new("GeometryNodeMenuSwitch")
    menu.data_type = "GEOMETRY"
    menu.enum_items.clear()
    for name in SHAPES:
        menu.enum_items.new(name)
    links.new(gi.outputs["Shape"], menu.inputs["Menu"])
    for name, geometry in zip(SHAPES, (ball, ball, capsule, tapered, cube.outputs["Mesh"], plane.outputs[0])):
        links.new(geometry, menu.inputs[name])
    # Divide out the object's scale: the drawing is the shape the solver uses, not a scaled one.
    divide = new("ShaderNodeVectorMath")
    divide.operation = "DIVIDE"
    divide.inputs[0].default_value = (1.0, 1.0, 1.0)
    links.new(scale, divide.inputs[1])
    unscale = new("GeometryNodeTransform")
    links.new(menu.outputs[0], unscale.inputs["Geometry"])
    links.new(divide.outputs[0], unscale.inputs["Scale"])
    links.new(unscale.outputs["Geometry"], go.inputs["Geometry"])
    return tree


def is_collider(obj):
    return obj is not None and obj.type == "MESH" and obj.waifu_physics_collider.is_collider


def modifier(obj):
    return obj.modifiers.get(MODIFIER)


def _identifiers(tree):
    return {item.name: item.identifier for item in tree.interface.items_tree if getattr(item, "in_out", "") == "INPUT"}


def values(obj):
    """The collider's shape and sizes, from its modifier."""
    md = modifier(obj)
    if md is None or md.node_group is None:
        return None
    ids = _identifiers(md.node_group)
    inputs = md.properties.inputs
    return {name: (getattr(inputs, ident).value if name != "Extent" else tuple(getattr(inputs, ident).value))
            for name, ident in ids.items()}


def set_value(obj, name, value):
    md = modifier(obj)
    setattr(getattr(md.properties.inputs, _identifiers(md.node_group)[name]), "value", value)
    obj.update_tag()                      # setting an input from Python does not re-evaluate by itself


def input_path(obj, name):
    """The animatable data path of one of the collider's inputs, for drawing or keying."""
    md = modifier(obj)
    return md, _identifiers(md.node_group)[name]


def default_sources(armature):
    """The armatures a group collides with when it names none: its own, and the armature it hangs from, if
    any (a garment rig parented to a body collides with the body's colliders)."""
    found = [armature]
    parent = armature.parent
    while parent is not None:
        if parent.type == "ARMATURE":
            found.append(parent)
            break
        parent = parent.parent
    return found


def sources(group):
    """The armatures whose colliders a group collides with: the ones it names, else the defaults."""
    if len(group.collider_sets):
        return [item.armature for item in group.collider_sets if item.armature is not None]
    return default_sources(group.id_data)


def armature_of(context):
    """The armature whose colliders the panel shows: the active armature, or a selected collider's."""
    obj = context.object
    if obj is None:
        return None
    if obj.type == "ARMATURE":
        return obj
    if is_collider(obj) and obj.parent is not None and obj.parent.type == "ARMATURE":
        return obj.parent
    return None


def all_of(armature):
    """Every collider parented to the armature, on or off, by name."""
    return sorted((obj for obj in armature.children if is_collider(obj)), key=lambda obj: obj.name)


def colliders_of(armature):
    """Enabled colliders parented to this armature."""
    return [obj for obj in armature.children if is_collider(obj) and obj.waifu_physics_collider.enabled]


def shape_of(obj, armature, cm):
    """The collider as a solver Shape in the armature's space, centimetres."""
    found = values(obj)
    if found is None:
        return None
    m = armature.matrix_world.inverted_safe() @ obj.matrix_world
    location, rotation, scale = m.decompose()
    sx, sy, sz = (abs(v) for v in scale)
    kind = KINDS.get(found["Shape"], SPHERE_OUTER)
    radius, radius1, length = found["Radius"], found["Radius 1"], found["Length"]
    if kind in (CAPSULE, TAPERED):
        rotation = rotation @ Y_TO_Z
        radius, radius1, length = radius * max(sx, sz), radius1 * max(sx, sz), length * sy
    elif kind in (SPHERE_OUTER, SPHERE_INNER):
        radius = radius * max(sx, sy, sz)
    extent = Vector(found["Extent"])
    extent = (extent.x * sx * cm, extent.y * sy * cm, extent.z * sz * cm)
    return Shape(kind, location=tuple(location * cm), rotation=(rotation.x, rotation.y, rotation.z, rotation.w),
                 radius=radius * cm, radius1=radius1 * cm, length=length * cm, extent=extent)


def short_name(bone_name):
    """A bone's name without VRoid's prefixes: J_Bip_C_Head -> Head, J_Bip_L_UpperArm -> L_UpperArm."""
    import re
    name = re.sub(r"^J_(Bip|Sec|Adj)_", "", bone_name)
    return re.sub(r"^C_", "", name) or bone_name


def _skinned_meshes(armature):
    return [obj for obj in bpy.data.objects if obj.type == "MESH" and not is_collider(obj)
            and any(md.type == "ARMATURE" and md.object == armature for md in obj.modifiers)]


def _weights(obj):
    """{vertex group index: (vertex indices, weights)} of a mesh, read once."""
    found = {}
    for vertex in obj.data.vertices:
        for entry in vertex.groups:
            found.setdefault(entry.group, ([], []))
            found[entry.group][0].append(vertex.index)
            found[entry.group][1].append(entry.weight)
    return {group: (np.array(indices), np.array(weights)) for group, (indices, weights) in found.items()}


def estimate_radius(armature, bone_name, skins=None):
    """How thick the body is around a bone, in world units: the 75th percentile of the distance from the bone
    of the skin weighted mostly to it, at rest. None when no mesh is skinned to it. skins: the armature's
    meshes with their weights, to reuse over many bones."""
    if skins is None:
        skins = [(obj, _weights(obj)) for obj in _skinned_meshes(armature)]
    bone = armature.data.bones[bone_name]
    world = np.array(armature.matrix_world)
    a = world[:3, :3] @ np.array(bone.head_local) + world[:3, 3]
    b = world[:3, :3] @ np.array(bone.tail_local) + world[:3, 3]
    ab = b - a
    length2 = float(ab @ ab) or 1.0e-12
    # The skin may be weighted to twins lying on the bone: children with the same head and tail (VRoid Swap's
    # J_Scale_ bones scale the skin under each J_Bip_ bone).
    names, stack = [bone_name], [bone]
    while stack:
        for child in stack.pop().children:
            if (child.head_local - bone.head_local).length < 1e-5 and (child.tail_local - bone.tail_local).length < 1e-5:
                names.append(child.name)
                stack.append(child)
    distances = []
    for obj, weights in skins:
        for name in names:
            group = obj.vertex_groups.get(name)
            if group is not None and group.index in weights:
                distances += _distances(obj, weights[group.index], a, ab, length2)
    if not distances:
        return None
    return float(np.percentile(np.concatenate(distances), 75.0))


def _distances(obj, weighted, a, ab, length2):
    """The world distances from the bone segment of the vertices weighted mostly to it (as a list of one array)."""
    indices, amounts = weighted
    chosen = indices[amounts >= 0.5]
    if len(chosen) < 8:
        chosen = indices[amounts >= 0.1]
    if not len(chosen):
        return []
    co = np.empty(len(obj.data.vertices) * 3, dtype=np.float32)
    obj.data.vertices.foreach_get("co", co)
    mw = np.array(obj.matrix_world)
    points = co.reshape(-1, 3)[chosen].astype(np.float64) @ mw[:3, :3].T + mw[:3, 3]
    t = np.clip(((points - a) @ ab) / length2, 0.0, 1.0)
    return [np.linalg.norm(points - (a + t[:, None] * ab), axis=1)]


def has_collider(armature, bone_name):
    return any(is_collider(obj) and obj.parent_bone == bone_name for obj in armature.children)


def from_bones(armature, bone_names, context=None):
    """A capsule along each bone, sized to the skin around it; bones with a collider already are skipped.
    Returns the new colliders."""
    skins = [(obj, _weights(obj)) for obj in _skinned_meshes(armature)]
    return [add(armature, name, "Capsule", context, skins) for name in bone_names if not has_collider(armature, name)]


def add(armature, bone_name, shape="Capsule", context=None, skins=None):
    """A new collider on a bone: centred on it, a capsule along it by default, as thick as the skin around it
    (a quarter of the bone's length where no mesh is skinned to it)."""
    collection = bpy.data.collections.get(COLLECTION)
    if collection is None:
        collection = bpy.data.collections.new(COLLECTION)
        (context or bpy.context).scene.collection.children.link(collection)
    mesh = bpy.data.meshes.new(f"{short_name(bone_name)} Collider")
    obj = bpy.data.objects.new(f"{short_name(bone_name)} Collider", mesh)
    collection.objects.link(obj)
    obj.waifu_physics_collider.is_collider = True
    obj.display_type = "WIRE"
    obj.show_in_front = True
    obj.hide_render = True
    md = obj.modifiers.new(MODIFIER, "NODES")
    md.node_group = node_group()
    pose_bone = armature.pose.bones[bone_name]
    obj.parent = armature
    obj.parent_type = "BONE"
    obj.parent_bone = bone_name
    bone_length = pose_bone.bone.length
    obj.matrix_world = armature.matrix_world @ pose_bone.matrix @ Matrix.Translation((0.0, bone_length / 2, 0.0))
    radius = estimate_radius(armature, bone_name, skins)
    if radius is None:
        radius = bone_length * 0.25
    else:
        radius /= max(obj.matrix_world.to_scale()[0], 1.0e-9)          # world size into the collider's own
    set_value(obj, "Shape", shape)
    set_value(obj, "Length", bone_length)
    set_value(obj, "Radius", radius)
    set_value(obj, "Radius 1", radius)
    return obj
