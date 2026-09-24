"""Group presets: starting points for the chains most rigs have.

A preset is a partial settings dict (serialize.settings_to_dict): what it
names is set, the rest of the group is left alone, and every curve is turned
off. Lengths are Blender units for a rig about 1.6 units tall.
"""
from . import curves, serialize

_NO_CURVES = {f"use_{setting}_curve": False for setting in curves.CURVED}

PRESETS = {
    "HAIR": ("Hair", "Light strands: loose, quick to settle, riding along with the head", {
        "damping": 0.1, "stiffness": 0.05, "world_damping_location": 0.8, "world_damping_rotation": 0.8,
        "radius": 0.01, "limit_angle": 0.0, "dummy_bone_length": 0.03,
        "bone_subdivision_count": 0, "bridge_count": 0, "planar_constraint": "NONE",
    }),
    "SKIRT": ("Skirt", "Panels hanging from the hips: heavier, held in shape, colliding as one surface", {
        "damping": 0.2, "stiffness": 0.1, "world_damping_location": 0.6, "world_damping_rotation": 0.6,
        "radius": 0.02, "limit_angle": 0.0, "dummy_bone_length": 0.05,
        "compliance": "LEATHER", "auto_child_dummy_links": True, "bridge_count": 1, "bridge_feedback": 1.0,
        "bone_subdivision_count": 0, "planar_constraint": "NONE",
    }),
}
ITEMS = [(key, label, description) for key, (label, description, _values) in PRESETS.items()]


def apply(group, key):
    serialize.settings_from_dict(group, {**_NO_CURVES, **PRESETS[key][2]})
