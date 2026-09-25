"""Link Chains: join neighbouring chains of a group, bone by bone at each depth.

Chains are put side by side (side_by_side), so a skirt's panels link to the
panels beside them whatever order they were selected in, and never closed into a
ring: the two end chains are left for the user to link, which closes it. Each
link joins the bones at the same depth below the roots -- the roots themselves
follow the animation --
and the group's Link Tips setting adds links between their tip and
subdivision points when the chains are built (Kawaii's automatic dummy links).
"""
import math

import numpy as np


def chain_subtree(obj, root, excluded=()):
    """Every bone of the chain under root, root first: what Waifu Physics simulates for it."""
    excluded = set(excluded)
    found, stack = [], [obj.pose.bones.get(root)]
    while stack:
        bone = stack.pop()
        if bone is None or bone.name in excluded:
            continue
        found.append(bone.name)
        stack.extend(reversed(bone.children))
    return found


def constrained_bones(obj, group):
    """Bones in a group's chains with an active constraint: Blender applies it after the
    simulation's output, so the simulation cannot move them."""
    excluded = {bone.name for bone in group.excluded}
    found, stack = [], [obj.pose.bones.get(root.name) for root in group.roots]
    while stack:
        bone = stack.pop()
        if bone is None or bone.name in excluded:
            continue
        if any(c.enabled and c.influence > 0 for c in bone.constraints):
            found.append(bone.name)
        stack.extend(bone.children)
    return found


def chain_bones(obj, root, excluded=()):
    """The bones of a chain from its root down, following the first child at each step."""
    bones, bone = [], obj.pose.bones.get(root)
    while bone is not None and bone.name not in excluded:
        bones.append(bone.name)
        children = [c for c in bone.children if c.name not in excluded]
        bone = children[0] if children else None
    return bones


def ordered(obj, roots):
    """Roots by angle around their centre, looking along the direction the chains hang."""
    heads = {r: np.array(obj.pose.bones[r].bone.head_local) for r in roots}
    downs = []
    for r in roots:
        chain = chain_bones(obj, r)
        tail = np.array(obj.pose.bones[chain[-1]].bone.tail_local)
        downs.append(tail - heads[r])
    axis = np.sum(downs, axis=0)
    if np.linalg.norm(axis) < 1e-9:
        axis = np.array([0.0, 0.0, -1.0])
    axis /= np.linalg.norm(axis)
    centre = np.mean(list(heads.values()), axis=0)
    helper = np.array([1.0, 0.0, 0.0]) if abs(axis[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    u = np.cross(axis, helper)
    u /= np.linalg.norm(u)
    v = np.cross(axis, u)
    angle = {r: math.atan2((heads[r] - centre) @ v, (heads[r] - centre) @ u) for r in roots}
    return sorted(roots, key=lambda r: angle[r])


def in_a_row(obj, roots):
    """Roots in order along the line they spread out on most (a cape's chains, left to right). An angle around
    their centre (ordered) cannot order chains in a straight row: they all sit at one of two angles."""
    heads = np.array([obj.pose.bones[r].bone.head_local for r in roots], dtype=float)
    spread = heads - heads.mean(axis=0)
    direction = np.linalg.svd(spread, full_matrices=False)[2][0] if len(roots) > 1 else np.zeros(3)
    return [roots[i] for i in np.argsort(spread @ direction, kind="stable")]


def side_by_side(obj, names):
    """Bones (or chain roots) in the order they sit side by side, end to end and never closed. Two orders are
    tried: round their centre, opened at the widest gap (a skirt, or a cape wrapped round the back), and along
    the line they spread out on most (a flat cape). The shorter path between neighbours wins: the wrong order
    zig-zags from one side to the other, so it is always the longer."""
    if len(names) < 3:
        return list(names)
    heads = {name: np.array(obj.pose.bones[name].bone.head_local) for name in names}
    ring = ordered(obj, names)
    gaps = [np.linalg.norm(heads[a] - heads[b]) for a, b in zip(ring, ring[1:] + ring[:1])]
    widest = int(np.argmax(gaps))
    arc = ring[widest + 1:] + ring[:widest + 1]

    def length(order):
        return sum(np.linalg.norm(heads[a] - heads[b]) for a, b in zip(order, order[1:]))
    return min((arc, in_a_row(obj, names)), key=length)


def neighbours(obj, names):
    """[(name, name)] of bones side by side (side_by_side): the last is not linked back to the first."""
    order = side_by_side(obj, names)
    return list(zip(order, order[1:]))


def pairs(obj, roots, excluded=()):
    """[(bone, bone)] linking each chain to its neighbour, at every depth below the roots (the roots do not
    move): a ladder's rungs between neighbouring chains, in side_by_side order, the two end chains left
    unlinked. Returns (the pairs, the roots in order)."""
    order = side_by_side(obj, roots)
    chains = [chain_bones(obj, r, excluded) for r in order]
    neighbours = list(zip(chains, chains[1:]))
    found = []
    for a, b in neighbours:
        for depth in range(1, min(len(a), len(b))):
            found.append((a[depth], b[depth]))
    return found, order
