"""Projects made outside the Project Manager still open with the Joinery addon on."""

from pathlib import Path

from joinery.bootstrap import PLUGIN, enable_plugin

BASE = 'config_version=5\n\n[application]\n\nconfig/name="T"\n'


def project(tmp_path: Path, text: str) -> Path:
    (tmp_path / "project.godot").write_text(text)
    return tmp_path


def test_adds_section_when_missing(tmp_path: Path) -> None:
    root = project(tmp_path, BASE)
    assert enable_plugin(root)
    text = (root / "project.godot").read_text()
    assert f'[editor_plugins]\n\nenabled=PackedStringArray("{PLUGIN}")' in text
    assert text.startswith(BASE.rstrip("\n"))
    assert not enable_plugin(root)


def test_keeps_other_plugins_and_later_sections(tmp_path: Path) -> None:
    other = '"res://addons/other/plugin.cfg"'
    root = project(
        tmp_path,
        BASE
        + f"\n[editor_plugins]\n\nenabled=PackedStringArray({other})\n\n[rendering]\n\nx=1\n",
    )
    assert enable_plugin(root)
    text = (root / "project.godot").read_text()
    assert f'enabled=PackedStringArray({other}, "{PLUGIN}")' in text
    assert text.endswith("[rendering]\n\nx=1\n")


def test_section_without_enabled_line(tmp_path: Path) -> None:
    root = project(tmp_path, BASE + "\n[editor_plugins]\n")
    assert enable_plugin(root)
    assert (
        f'enabled=PackedStringArray("{PLUGIN}")' in (root / "project.godot").read_text()
    )


def test_already_enabled_is_untouched(tmp_path: Path) -> None:
    text = BASE + f'\n[editor_plugins]\n\nenabled=PackedStringArray("{PLUGIN}")\n'
    root = project(tmp_path, text)
    assert not enable_plugin(root)
    assert (root / "project.godot").read_text() == text
