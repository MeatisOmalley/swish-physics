"""Viewport overlay: a group's links drawn as lines between the bones they join."""
import bpy
import gpu
from gpu_extras.batch import batch_for_shader

_handle = None
COLOURS = ((0.35, 0.8, 1.0, 1.0), (1.0, 0.6, 0.25, 1.0), (0.6, 1.0, 0.4, 1.0), (1.0, 0.45, 0.8, 1.0))


def _draw():
    context = bpy.context
    scene = context.scene
    if scene is None or not scene.waifu_physics.show_links:
        return
    lines, colours = [], []
    for obj in scene.objects:
        if obj.type != "ARMATURE" or not obj.visible_get():
            continue
        bones = obj.pose.bones
        world = obj.matrix_world
        for index, group in enumerate(obj.waifu_physics.groups):
            if not group.enabled:
                continue
            colour = COLOURS[index % len(COLOURS)]
            for link in group.links:
                a, b = bones.get(link.bone_a), bones.get(link.bone_b)
                if a is None or b is None:
                    continue
                lines += [world @ a.head, world @ b.head]
                colours += [colour, colour]
    if not lines:
        return
    shader = gpu.shader.from_builtin("POLYLINE_SMOOTH_COLOR")
    region = context.region
    shader.uniform_float("viewportSize", (region.width, region.height))
    shader.uniform_float("lineWidth", 2.0)
    batch = batch_for_shader(shader, "LINES", {"pos": [tuple(p) for p in lines], "color": colours})
    gpu.state.blend_set("ALPHA")
    gpu.state.depth_test_set("NONE")
    batch.draw(shader)
    gpu.state.blend_set("NONE")


def register():
    global _handle
    if _handle is None:
        _handle = bpy.types.SpaceView3D.draw_handler_add(_draw, (), "WINDOW", "POST_VIEW")


def unregister():
    global _handle
    if _handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_handle, "WINDOW")
        _handle = None
