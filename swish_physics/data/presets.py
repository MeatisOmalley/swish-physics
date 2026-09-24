"""Group presets: starting points for the chains most rigs have.

A preset is a partial settings dict (serialize.settings_to_dict): what it
names is set, the rest of the group is left alone, and every curve is turned
off. Lengths are Blender units for a rig about 1.6 units tall.
"""
import math

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


# Kawaii's own Procedural Wind presets (KawaiiPhysicsWindPresetDataAsset.cpp, GetDefaultPresets), in its
# units: forces in centimetres a second, periods in seconds, angles in degrees. Its forces act every step
# and build up like velocity, so small numbers push harder than they look.
WIND_PRESETS = {
    "BREEZE": ("Breeze", dict(constant=2.0, sway=1.0, sway_period=3.0, ripple=1.0, ripple_period=1.2,
                              ripple_delay=120.0, cycle_min=0.6, cycle_max=1.0, cycle_period=20.0,
                              random=0.5, random_period=0.8, noise_angle=5.0)),
    "STRONG": ("Strong", dict(constant=8.0, sway=4.0, sway_period=1.6, ripple=3.0, ripple_period=0.5,
                              ripple_delay=120.0, cycle_min=0.7, cycle_max=1.3, cycle_period=12.0,
                              random=2.0, random_period=0.5, noise_angle=10.0)),
    "STORM": ("Storm", dict(constant=15.0, sway=10.0, sway_period=0.9, ripple=8.0, ripple_period=0.3,
                            ripple_delay=180.0, cycle_min=0.5, cycle_max=1.6, cycle_period=7.0,
                            random=6.0, random_period=0.3, noise_angle=20.0)),
}
WIND_ITEMS = [(key, label, f"Kawaii's {label} wind preset") for key, (label, _values) in WIND_PRESETS.items()]
_WIND_FORCES = ("constant", "sway", "ripple", "random")
_WIND_ANGLES = ("ripple_delay", "noise_angle")


def apply_wind(force, key, cm_per_unit):
    """Set a Procedural Wind force to one of Kawaii's presets, converted to Blender units."""
    for name, value in WIND_PRESETS[key][1].items():
        if name in _WIND_FORCES:
            value = value / cm_per_unit
        elif name in _WIND_ANGLES:
            value = math.radians(value)
        setattr(force, name, value)
