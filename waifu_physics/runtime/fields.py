"""The scene's force fields, read for the solver's port of Blender's effectors (solver/fields.py)."""
import numpy as np

from ..solver.fields import SHAPES, SUPPORTED, FieldSpec


def spec_of(obj, weight=1.0):
    """A field object as a FieldSpec, or None when it is not one the chains can feel."""
    field = obj.field
    if field is None or field.type not in SUPPORTED or field.shape not in SHAPES or not field.apply_to_location:
        return None
    world = obj.matrix_world
    axis = np.array(world.to_3x3().col[2], dtype=np.float64)
    length = np.linalg.norm(axis)
    return FieldSpec(
        kind=field.type, location=np.array(world.translation, dtype=np.float64),
        axis=axis / length if length > 0.0 else np.array([0.0, 0.0, 1.0]),
        strength=field.strength, damping=field.harmonic_damping, flow=field.flow, noise=field.noise,
        seed=field.seed, shape=field.shape, falloff=field.falloff_type, power=field.falloff_power,
        use_min=field.use_min_distance, min_distance=field.distance_min,
        use_max=field.use_max_distance, max_distance=field.distance_max,
        radial_power=field.radial_falloff, use_min_radial=field.use_radial_min, min_radial=field.radial_min,
        use_max_radial=field.use_radial_max, max_radial=field.radial_max, z_direction=field.z_direction,
        gravitation=field.use_gravity_falloff, size=field.size if field.type == "TURBULENCE" else field.rest_length,
        global_coordinates=field.use_global_coords, weight=weight)


def field_objects(scene, collection=None):
    """Visible field objects: the collection's, if the group names one, else the scene's."""
    objects = collection.all_objects if collection is not None else scene.objects
    found = []
    for obj in objects:
        if obj.field is None or obj.field.type == "NONE":
            continue
        try:
            if not obj.visible_get():
                continue
        except RuntimeError:                  # not in the view layer
            continue
        found.append(obj)
    return found


def specs(scene, collection=None, weight=1.0):
    return [spec for spec in (spec_of(obj, weight) for obj in field_objects(scene, collection)) if spec is not None]
