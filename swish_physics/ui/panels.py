"""The Swish sidebar tab in the 3D viewport."""
import bpy


class SWISH_PT_main(bpy.types.Panel):
    bl_idname = "SWISH_PT_main"
    bl_label = "Swish Physics"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Swish"

    def draw(self, context):
        self.layout.label(text="No physics groups yet", icon="INFO")


CLASSES = (SWISH_PT_main,)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
