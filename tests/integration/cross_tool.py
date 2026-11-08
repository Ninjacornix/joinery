"""Native Blender source -> Godot instance -> one reviewed transaction.

Uses a temporary technical greybox fixture and a mocked approval check. This
does not approve or create finished artwork in any real user's game project.
"""

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import patch

from joinery.gateway import ProjectGateway, result_text
from joinery.transactions import FileTransaction


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="joinery-cross-tool-fixture-") as directory:
        project = Path(directory)
        (project / "project.godot").write_text("config_version=5\n")
        (project / "joinery.toml").write_text('[tools]\nenabled=["godot","blender"]\n')
        (project / "joinery.lock").write_text("version=1\n")
        (project / "art").mkdir()
        (project / "art/bible.md").write_text("Automated technical fixture; no game artwork.")
        gateway = ProjectGateway(project)
        try:
            with patch("joinery.gateway.approved", return_value=True):
                await gateway.discover()
                result = await gateway.call("joinery-blender-ops", "operate_document", {"tool": "blender", "source": "art/sources/fixture.blend", "code": 'bpy.ops.mesh.primitive_cube_add(size=1)\nbpy.context.object.name="Fixture"', "exports": ["art/models/fixture.glb"]})
                assert not result.is_error, result_text(result)
                scene = '[gd_scene load_steps=2 format=3]\n[ext_resource type="PackedScene" path="res://art/models/fixture.glb" id="1"]\n[node name="FixtureLevel" type="Node3D"]\n[node name="Fixture" parent="." instance=ExtResource("1")]\n'
                result = await gateway.call("godot", "write_file", {"projectPath": str(project), "filePath": "fixture.tscn", "content": scene})
                assert not result.is_error, result_text(result)
                preview = await gateway.finish()
                assert preview and preview["media"]
                assert not (project / "fixture.tscn").exists()
                await gateway.decide(preview["transaction_id"], True)
                assert (project / "fixture.tscn").is_file()
                assert (project / "art/sources/fixture.blend").is_file()
                assert (project / "art/models/fixture.glb").is_file()
                accepted = FileTransaction.load(project, preview["transaction_id"])
                accepted.revert()
                assert not (project / "fixture.tscn").exists()
                assert not (project / "art/sources/fixture.blend").exists()
                print("Blender -> immediate Godot import/instance -> review/accept/revert: PASS")
        finally:
            await gateway.close()


if __name__ == "__main__":
    asyncio.run(main())
