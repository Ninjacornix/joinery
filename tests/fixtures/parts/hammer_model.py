# Operation code for the part-selection fixture: a small hammer made in Blender
# from primitives and procedurally written pixels. build.py wraps it in Joinery's
# own Blender recipe script (save, texture export, GLB export with part names)
# and runs it in the offline creative worker. No asset is downloaded.
#
# Steel: base colour, roughness, and a height map that drives both a Bump
# (into the shader normal) and the material Displacement.
# Wood: base colour and a packed ORM map (Green is roughness, Blue is metallic).
# Head.001 is Steel; Handle is Wood with a Steel end cap (a second slot).
import math

import bpy


def image(name, pixel, non_color=False, size=32):
    picture = bpy.data.images.new(name, size, size, alpha=False, is_data=non_color)
    if non_color:
        picture.colorspace_settings.name = "Non-Color"
    values = []
    for y in range(size):
        for x in range(size):
            values += [*pixel(x / (size - 1), y / (size - 1)), 1.0]
    picture.pixels.foreach_set(values)
    picture.pack()
    return picture


def texture_node(tree, picture, label, x, y):
    node = tree.nodes.new("ShaderNodeTexImage")
    node.image = picture
    node.name = node.label = label
    node.location = (x, y)
    return node


def material(name):
    made = bpy.data.materials.new(name)
    made.use_nodes = True
    return made, made.node_tree


steel, tree = material("Steel")
bsdf = tree.nodes["Principled BSDF"]
output = tree.nodes["Material Output"]
base = texture_node(
    tree,
    image("steel_base", lambda u, v: (0.55 + 0.1 * u, 0.56 + 0.1 * u, 0.6 + 0.1 * v)),
    "Base Color Map",
    -700,
    300,
)
rough = texture_node(
    tree,
    image("steel_rough", lambda u, v: (0.2 + 0.6 * u,) * 3, non_color=True),
    "Roughness Map",
    -700,
    0,
)
height = texture_node(
    tree,
    image(
        "steel_height",
        lambda u, v: (0.5 + 0.5 * math.sin(u * 12.0) * math.sin(v * 12.0),) * 3,
        non_color=True,
    ),
    "Height Map",
    -700,
    -300,
)
bump = tree.nodes.new("ShaderNodeBump")
bump.location = (-300, -300)
displacement = tree.nodes.new("ShaderNodeDisplacement")
displacement.location = (-300, -550)
tree.links.new(base.outputs["Color"], bsdf.inputs["Base Color"])
tree.links.new(rough.outputs["Color"], bsdf.inputs["Roughness"])
tree.links.new(height.outputs["Color"], bump.inputs["Height"])
tree.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
tree.links.new(height.outputs["Color"], displacement.inputs["Height"])
tree.links.new(displacement.outputs["Displacement"], output.inputs["Displacement"])
bsdf.inputs["Metallic"].default_value = 1.0

wood, tree = material("Wood")
bsdf = tree.nodes["Principled BSDF"]
wood_base = texture_node(
    tree,
    image("wood_base", lambda u, v: (0.45 + 0.1 * math.sin(v * 40.0), 0.28, 0.12)),
    "Wood Base",
    -700,
    300,
)
orm = texture_node(
    tree,
    image("wood_orm", lambda u, v: (1.0, 0.6 + 0.2 * v, 0.0), non_color=True),
    "Wood ORM",
    -700,
    0,
)
split = tree.nodes.new("ShaderNodeSeparateColor")
split.location = (-350, 0)
tree.links.new(wood_base.outputs["Color"], bsdf.inputs["Base Color"])
tree.links.new(orm.outputs["Color"], split.inputs["Color"])
tree.links.new(split.outputs["Green"], bsdf.inputs["Roughness"])
tree.links.new(split.outputs["Blue"], bsdf.inputs["Metallic"])

bpy.ops.mesh.primitive_cylinder_add(
    vertices=16, radius=0.03, depth=0.6, location=(0.0, 0.0, 0.3)
)
handle = bpy.context.object
handle.name = "Handle"
handle.data.name = "HandleMesh"
handle.data.materials.append(wood)
handle.data.materials.append(steel)
for polygon in handle.data.polygons:
    # The bottom cap is a steel end cap.
    polygon.material_index = 1 if polygon.center.z < -0.25 else 0

bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0.0, 0.0, 0.64))
head = bpy.context.object
# A dotted name, as Blender makes for copies: Godot shows it as Head_001.
head.name = "Head.001"
head.data.name = "HeadMesh"
head.scale = (0.24, 0.08, 0.08)
bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
head.data.materials.append(steel)
head.shape_key_add(name="Basis", from_mix=False)
dent = head.shape_key_add(name="Dent", from_mix=False)
for point in dent.data:
    if point.co.z > 0.03:
        point.co.z -= 0.01

for obj in (handle, head):
    for other in bpy.context.view_layer.objects:
        other.select_set(other is obj)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(island_margin=0.02)
    bpy.ops.object.mode_set(mode="OBJECT")
