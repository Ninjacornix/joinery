import json
from pathlib import Path

from joinery.catalog import CONFIG_PATH
from joinery.cli import check_mcp_config


def write(tmp_path: Path, servers: dict[str, dict[str, object]]) -> None:
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": servers}))


def test_custom_server_may_use_anything(tmp_path: Path) -> None:
    config = tmp_path / "joinery.toml"
    config.write_text(
        '[tools]\n[[tools.custom]]\nid = "b"\nname = "B"\ndomain = "3d"\n'
        'server = "my-blender"\ncommand = "uvx"\nargs = ["blender-mcp"]\n'
    )
    write(tmp_path, {"my-blender": {"command": "uvx", "args": ["blender-mcp"]}})
    assert check_mcp_config(tmp_path, config) == []
    assert check_mcp_config(tmp_path, CONFIG_PATH)


def test_absent_config_passes(tmp_path: Path) -> None:
    assert check_mcp_config(tmp_path) == []


def test_allowed_server_passes(tmp_path: Path) -> None:
    write(tmp_path, {"godot": {"command": "godot-ai", "args": ["attach"]}})
    assert check_mcp_config(tmp_path) == []


def test_unknown_server_fails(tmp_path: Path) -> None:
    write(tmp_path, {"rodin": {"command": "x"}})
    assert check_mcp_config(tmp_path)


def test_allowed_name_with_denied_package_fails(tmp_path: Path) -> None:
    write(tmp_path, {"blender": {"command": "uvx", "args": ["blender-mcp"]}})
    assert any("denied package" in e for e in check_mcp_config(tmp_path))


def test_allowed_name_with_rejected_source_fails(tmp_path: Path) -> None:
    url = "git+https://github.com/ahujasid/blender-mcp"
    write(tmp_path, {"blender": {"command": "uvx", "args": ["--from", url, "x"]}})
    assert any("rejected source" in e for e in check_mcp_config(tmp_path))
