"""Execute the verified upstream plugin handlers inside native GIMP's batch host."""

import json
import runpy
from pathlib import Path
from unittest.mock import patch

from gi.repository import Gimp

# GIMP already owns this batch process. Suppress only the module's attempt to
# enter a second plug-in registration loop; all document APIs remain real.
with patch.object(Gimp, "main", lambda *_args: None):
    plugin_type = runpy.run_path(
        "/work/gimp-mcp-plugin.py", run_name="joinery_gimp_native_plugin"
    )["MCPPlugin"]

# Batch python already owns a GIMP PlugIn/channel. A second GI PlugIn instance
# would abort in libgimp. Bind the exact upstream handler methods to a plain
# receiver; every GIMP image/layer/PDB operation below still executes natively.
methods = {
    name: value
    for name, value in vars(plugin_type).items()
    if callable(value) and not name.startswith("__")
}
plugin = type("NativeGimpHandlers", (), methods)()
plugin.context = {"Gimp": Gimp}
plugin.port = 0
plugin.running = False

results = []


def call(name, /, **params):
    result = plugin.execute_command(json.dumps({"type": name, "params": params}))
    assert result["status"] == "success", result
    results.append({"command": name, "result": result["results"]})
    return result["results"]


call("new_canvas", width=16, height=16, name="TechnicalFixture", fill="transparent")
call("create_layer", name="Paint", fill="transparent", image_index=0)
call(
    "fill_rectangle",
    x=0,
    y=0,
    width=16,
    height=16,
    color="#ff0000",
    layer_name="Paint",
    image_index=0,
)
call("save_xcf", file_path="/work/source.xcf", image_index=0)
assert Path("/work/source.xcf").stat().st_size > 0
call(
    "export_image",
    file_path="/work/red.png",
    format="png",
    flatten=False,
    image_index=0,
)
assert Path("/work/red.png").stat().st_size > 0
call("rename_layer", old_name="Paint", new_name="Accent", image_index=0)
call(
    "fill_rectangle",
    x=0,
    y=0,
    width=16,
    height=16,
    color="#0000ff",
    layer_name="Accent",
    image_index=0,
)
call("save_xcf", file_path="/work/updated.xcf", image_index=0)
call(
    "export_image",
    file_path="/work/blue.png",
    format="png",
    flatten=False,
    image_index=0,
)
image = Gimp.get_images()[0]
assert "Accent" in [layer.get_name() for layer in image.get_layers()]
assert Path("/work/updated.xcf").stat().st_size > 0
assert Path("/work/red.png").read_bytes() != Path("/work/blue.png").read_bytes()
Path("/work/results.json").write_text(json.dumps(results))
print("GIMP_NATIVE_HANDLERS_OK", flush=True)

from PyQt5.QtGui import QImage

assert list(QImage("/work/red.png").pixelColor(8, 8).getRgb()) == [255, 0, 0, 255]
assert list(QImage("/work/blue.png").pixelColor(8, 8).getRgb()) == [0, 0, 255, 255]
print("GIMP_NATIVE_PIXELS_OK", flush=True)
