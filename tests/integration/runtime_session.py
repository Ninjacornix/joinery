import asyncio
import tempfile
from pathlib import Path

from joinery.gateway import ProjectGateway, result_text
from joinery.submission import check_and_open


async def main():
    with tempfile.TemporaryDirectory(prefix="joinery-runtime-fixture-") as d:
        p = Path(d)
        (p / "project.godot").write_text(
            'config_version=5\n[application]\nrun/main_scene="res://main.tscn"\n[rendering]\nrenderer/rendering_method="gl_compatibility"\n'
        )
        (p / "main.tscn").write_text(
            '[gd_scene format=3]\n[node name="RuntimeFixture" type="Node3D"]\n'
        )
        (p / "joinery.toml").write_text('[tools]\nenabled=["godot"]\n')
        g = ProjectGateway(p)
        try:
            await g.discover()
            r = await g.call("godot", "run_project", {"projectPath": str(p)})
            assert not r.is_error, result_text(r)
            r = await g.call("godot", "game_get_scene_tree", {})
            print(result_text(r), flush=True)
            assert not r.is_error
            preview = await check_and_open(g)
            assert preview is None, preview
            assert "McpInteractionServer" not in (p / "project.godot").read_text()
            print("Owned offline runtime inspection/cleanup: PASS")
        finally:
            await g.close()


asyncio.run(main())
