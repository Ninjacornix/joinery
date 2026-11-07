"""Build the testbed sky in Blender. Replayed by `joinery rebuild`.

Operation log for art/sky/dusk_sky.png and art/sky/dusk_sky.blend. Blender's
procedural sky model is rendered to an equirectangular image, which Godot uses
as a PanoramaSkyMaterial. The .blend is the editable source: changing the sun
angle or turbidity and re-rendering is how the sky changes.

This is physical sky simulation, not an image generator: the model takes a sun
position and atmosphere parameters and computes scattering.

Art rules this follows:
  dusk, warm low sun, modest contrast
  1024x512 equirectangular, which is plenty for a stylised panorama sky

Run: blender --background --factory-startup --python recipes/sky.py
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import bpy

BLEND_OUT = Path("art/sky/src/dusk_sky.blend")
IMAGE_OUT = Path("art/sky/dusk_sky.png")
WIDTH, HEIGHT = 1024, 512
SAMPLES = 16  # Sky only, no geometry: few samples are enough and keep CI quick.


def clear_scene() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.preferences.filepaths.save_version = 0


def build_world() -> None:
    """A dusk sky from the procedural sky model.

    Blender renames these between versions: 5.2 dropped "NISHITA" for
    "MULTIPLE_SCATTERING" and "dust_density" for "aerosol_density". So pick the
    best available model and only set parameters this build actually has, which
    degrades on a rename instead of failing the whole recipe.
    """
    world = bpy.data.worlds.new("Dusk")
    bpy.context.scene.world = world
    world.use_nodes = True
    nodes, links = world.node_tree.nodes, world.node_tree.links
    nodes.clear()

    out = nodes.new("ShaderNodeOutputWorld")
    out.location = (300, 0)
    bg = nodes.new("ShaderNodeBackground")
    bg.location = (100, 0)
    sky = nodes.new("ShaderNodeTexSky")
    sky.location = (-200, 0)

    available = [i.identifier for i in sky.bl_rna.properties["sky_type"].enum_items]
    for preferred in ("MULTIPLE_SCATTERING", "NISHITA", "HOSEK_WILKIE"):
        if preferred in available:
            sky.sky_type = preferred
            break

    # Sun just above the horizon, warm and low; haze is what reddens a dusk sky.
    wanted = {
        "sun_elevation": math.radians(4.5),
        "sun_rotation": math.radians(215.0),
        "sun_intensity": 0.9,
        "altitude": 0,
        "air_density": 1.2,
        "aerosol_density": 2.4,
        "dust_density": 2.4,
        # Dim warm earth rather than a pure black lower hemisphere, which also
        # softens the horizon line where the sky meets level geometry.
        "ground_albedo": 0.12,
    }
    for name, value in wanted.items():
        if name in sky.bl_rna.properties:
            setattr(sky, name, value)
    bg.inputs["Strength"].default_value = 1.0

    links.new(sky.outputs["Color"], bg.inputs["Color"])
    links.new(bg.outputs["Background"], out.inputs["Surface"])


def build_camera() -> bpy.types.Object:
    """An equirectangular camera, so one render covers the whole sphere."""
    cam_data = bpy.data.cameras.new("PanoCam")
    cam_data.type = "PANO"
    # Blender moved panorama_type between versions; set whichever exists.
    if hasattr(cam_data, "panorama_type"):
        cam_data.panorama_type = "EQUIRECTANGULAR"
    elif hasattr(cam_data, "cycles"):
        cam_data.cycles.panorama_type = "EQUIRECTANGULAR"
    cam = bpy.data.objects.new("PanoCam", cam_data)
    bpy.context.scene.collection.objects.link(cam)
    # Level horizon: looking along +Y with Z up.
    cam.location = (0.0, 0.0, 1.6)
    cam.rotation_euler = (math.radians(90.0), 0.0, 0.0)
    bpy.context.scene.camera = cam
    return cam


def main() -> None:
    clear_scene()
    build_world()
    build_camera()

    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.samples = SAMPLES
    # Cycles is not bit-reproducible by default: adaptive sampling decides when
    # to stop from noise estimates that shift with thread scheduling, and the
    # thread count itself varies by machine. Fix the seed, sample every pixel
    # the same number of times, and render single-threaded. Slower, but the
    # render is identical on every machine, which is what makes it verifiable.
    scene.cycles.seed = 20261107
    scene.cycles.use_animated_seed = False
    scene.cycles.use_adaptive_sampling = False
    scene.cycles.use_denoising = False
    scene.render.threads_mode = "FIXED"
    scene.render.threads = 1
    scene.render.resolution_x = WIDTH
    scene.render.resolution_y = HEIGHT
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.render.image_settings.compression = 15
    # No metadata stamped into the PNG, so two renders compare byte for byte.
    scene.render.use_stamp = False
    scene.render.use_stamp_note = False

    IMAGE_OUT.parent.mkdir(parents=True, exist_ok=True)
    scene.render.filepath = str(IMAGE_OUT.resolve())
    bpy.ops.render.render(write_still=True)
    # Godot imports everything it finds, and this file has no mesh to import,
    # so it lives behind a .gdignore as an editable source only.
    BLEND_OUT.parent.mkdir(parents=True, exist_ok=True)
    (BLEND_OUT.parent / ".gdignore").write_text("")
    bpy.ops.wm.save_as_mainfile(filepath=str(BLEND_OUT.resolve()))
    print(f"JOINERY_BUILT {IMAGE_OUT} {BLEND_OUT} {WIDTH}x{HEIGHT} samples={SAMPLES}")


if __name__ == "__main__":
    main()
    sys.exit(0)
