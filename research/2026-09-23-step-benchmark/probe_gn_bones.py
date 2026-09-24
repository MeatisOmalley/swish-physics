import bpy
print("GN| blender", bpy.app.version_string)
names = sorted(t for t in dir(bpy.types) if t.startswith(("GeometryNode", "FunctionNode")))
print("GN| node types:", len(names))
hits = [n for n in names if any(k in n.lower() for k in ("bone", "armature", "pose", "skin", "deform"))]
print("GN| bone/armature/pose related:", hits)
tree = bpy.data.node_groups.new("probe", "GeometryNodeTree")
for n in hits:
    try:
        node = tree.nodes.new(n)
        print("GN|  ", n, "| inputs:", [s.name for s in node.inputs], "| outputs:", [s.name for s in node.outputs])
    except Exception as exc:
        print("GN|  ", n, "cannot add:", exc)
