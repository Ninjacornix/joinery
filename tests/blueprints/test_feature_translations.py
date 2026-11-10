"""The translations feature: languages, PO/CSV files in `locale/`, the
pseudo-locale and the text collector.

Static checks cover the unit, its write roots and the per-blueprint adapters.
In stock Godot, `res://tests/features/translations/translations_test.gd`
switches languages (menus update at once), checks the Language settings
page, the pseudo-locale and that the choice survives a restart, and
`res://features/translations/tools/extract_pot.gd` is run on real projects
and on a fixture with every kind of text it must and must not collect.

The shipped `adapters/<blueprint>/overlay/locale/messages.pot` files and the
POT source lists at the end of `feature.toml` are generated. When game text
changes, rebuild them with:

    JOINERY_REFRESH_POT=1 uv run pytest tests/blueprints/test_feature_translations.py -k shipped
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from joinery import blueprints
from tests.blueprints.godot_run import (
    TEST_ARGS,
    Completed,
    boot,
    godot_binary,
    godot_env,
    make_project,
    needs_godot,
    run_godot,
)
from tests.blueprints.test_core import check_overlay

FEATURE = "translations"
FEATURE_DIR = blueprints.FEATURES_DIR / FEATURE
TEST = "res://tests/features/translations/translations_test.gd"
EXTRACT = "res://features/translations/tools/extract_pot.gd"
ROOTS = ("features/translations/", "tests/features/translations/", "locale/")
POT_FILES = "internationalization/locale/translations_pot_files"
TRANSLATIONS = "internationalization/locale/translations"
#: Where the generated part of feature.toml starts.
GENERATED_MARK = "# --- Per blueprint:"
#: Files of these folders belong in a blueprint's POT source list.
POT_SOURCE_PREFIXES = ("res://core/", "res://game/", "res://features/translations/")
REFRESH = os.environ.get("JOINERY_REFRESH_POT") == "1"

GERMAN_PO = r"""msgid ""
msgstr ""
"Language: de\n"
"Content-Type: text/plain; charset=UTF-8\n"
"Plural-Forms: nplurals=2; plural=(n != 1);\n"

msgid "Play"
msgstr "Spielen"

msgid "Settings"
msgstr "Einstellungen"

msgid "Quit"
msgstr ""

msgid "Player %d: %d pair"
msgid_plural "Player %d: %d pairs"
msgstr[0] "Spieler %d: %d Paar"
msgstr[1] "Spieler %d: %d Paare"
"""

FIXTURE_GD = r'''extends Control
## Docstrings are not text.

const ID := &"not_text"
const LABEL_TEXT := "A constant"

var node_name: String = "NodeName"


func _ready() -> void:
	name = "AlsoNotText"
	var label: Label = Label.new()
	label.text = "Assigned text"
	label.tooltip_text = "A tooltip"
	label.text = "Formatted %d" % 3
	label.text = "Joined " + node_name
	var heading: String = "Heading text" if visible else "Other heading"
	print(tr("Translated"))
	print(tr("With context", "menu"))
	print(tr_n("One apple", "%d apples", 2))
	print(TranslationServer.translate("Server text"))
	# tr("In a comment")
	var nested: String = tr("Escaped \"quote\"\nnext line")
	Menus.add_entry(&"main", &"id_only", "Entry label", func() -> void: pass, 5)
	add_child(UiStyle.button("Button text"))
	add_child(_custom_row("Row text", 3))
	add_child(UiStyle.page(str("dynamic"), null))
	var path: String = "res://game/level.tscn"
	label.text = r"Raw text\n"
	label.placeholder_text = 'Single quotes'
	var widths: Array[String] = ["not", "text"]
	print(get_node_or_null(^"Some/Path"), heading, nested, path, widths, ID, LABEL_TEXT)
	label.text = """Long
text"""
	label.text = "%d" % 5


func _custom_row(text: String, count: int) -> Control:
	var row: HBoxContainer = HBoxContainer.new()
	row.name = "Row%d" % count
	row.tooltip_text = text
	return row
'''

FIXTURE_TSCN = """[gd_scene format=3 uid="uid://cq3fixturescene"]

[node name="Root" type="Control"]

[node name="Shown" type="Label" parent="."]
text = "Scene label"
tooltip_text = "Scene tooltip"

[node name="Untranslated" type="Label" parent="."]
auto_translate_mode = 2
text = "Player name"

[node name="Multi" type="Label" parent="."]
text = "Two
lines with \\"quotes\\""
"""

FIXTURE_WANTED = {
    ("", "Assigned text", ""),
    ("", "A tooltip", ""),
    ("", "Heading text", ""),
    ("", "Other heading", ""),
    ("", "Translated", ""),
    ("menu", "With context", ""),
    ("", "One apple", "%d apples"),
    ("", "Server text", ""),
    ("", 'Escaped "quote"\nnext line', ""),
    ("", "Entry label", ""),
    ("", "Button text", ""),
    ("", "Row text", ""),
    ("", "Raw text\\n", ""),
    ("", "Single quotes", ""),
    ("", "Long\ntext", ""),
    ("", "Scene label", ""),
    ("", "Scene tooltip", ""),
    ("", 'Two\nlines with "quotes"', ""),
}


# --- helpers -----------------------------------------------------------------


def _feature() -> blueprints.Feature:
    return blueprints.load_feature(FEATURE_DIR)


def _blueprint_ids() -> list[str]:
    return sorted(b.id for b in blueprints.list_blueprints())


def _unquote(text: str) -> str:
    body = text.strip()
    assert body.startswith('"') and body.endswith('"'), body
    out: list[str] = []
    chars = iter(body[1:-1])
    escapes = {"n": "\n", "t": "\t", '"': '"', "\\": "\\"}
    for char in chars:
        out.append(escapes.get(next(chars, ""), "") if char == "\\" else char)
    return "".join(out)


@dataclass
class PoEntry:
    msgid: str = ""
    plural: str = ""
    context: str = ""
    msgstr: list[str] = field(default_factory=list)
    refs: list[str] = field(default_factory=list)
    has_id: bool = False


_PO_LINE = re.compile(r"(msgctxt|msgid_plural|msgid|msgstr(?:\[\d+\])?)\s+(\".*\")$")


def parse_po(text: str) -> list[PoEntry]:
    """Entries of a PO/POT file (header and `#~` entries left out)."""
    entries: list[PoEntry] = []
    current: PoEntry | None = None
    target = ""

    def fresh() -> PoEntry:
        entry = PoEntry()
        entries.append(entry)
        return entry

    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#:"):
            if current is None or current.has_id:
                current = fresh()
            current.refs.append(line[2:].strip())
            continue
        if not line or line.startswith("#"):
            continue
        match = _PO_LINE.match(line)
        if match:
            target, value = match.group(1), _unquote(match.group(2))
            if target in ("msgctxt", "msgid") and (current is None or current.has_id):
                current = fresh()
            assert current is not None
            if target == "msgctxt":
                current.context = value
            elif target == "msgid":
                current.msgid, current.has_id = value, True
            elif target == "msgid_plural":
                current.plural = value
            else:
                current.msgstr.append(value)
        elif line.startswith('"') and current is not None:
            value = _unquote(line)
            if target == "msgctxt":
                current.context += value
            elif target == "msgid":
                current.msgid += value
            elif target == "msgid_plural":
                current.plural += value
            elif target.startswith("msgstr"):
                current.msgstr[-1] += value
    return [e for e in entries if e.msgid or e.context]


def _msgids(text: str) -> set[str]:
    return {e.msgid for e in parse_po(text)}


def _extract(project: Path, out: Path, *args: str) -> Completed:
    return run_godot(project, EXTRACT, (f"--out={out}", *args), timeout=180, marker="extract_pot")


def _settings(tmp_path: Path, name: str = "settings.cfg") -> str:
    return f"--settings-path={tmp_path / name}"


def _reimport(project: Path) -> None:
    """Import again (new CSV files), outside the once-per-project helper."""
    binary = godot_binary()
    assert binary is not None
    result = subprocess.run(
        [str(binary), "--headless", "--path", str(project), "--import"],
        capture_output=True,
        text=True,
        timeout=180,
        env=godot_env(project),
        check=False,
    )
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]


# --- the unit ----------------------------------------------------------------


def test_translations_feature_declares_its_merges() -> None:
    feature = _feature()
    assert feature.name == "Translations"
    assert feature.extra_roots == ["locale"]
    assert feature.blueprints == ["*"]
    assert feature.requires == [] and feature.conflicts == [] and feature.external == []
    assert feature.merge.autoload == {"Language": "res://features/translations/language.gd"}
    assert feature.merge.settings["internationalization/locale/fallback"] == "en"
    assert feature.merge.settings["internationalization/pseudolocalization/expansion_ratio"] == 0.3
    assert feature.merge.append == {TRANSLATIONS: ["res://locale/en.po"]}
    assert feature.merge.input == {}


def test_translations_overlays_are_code_and_text_only() -> None:
    overlays = [FEATURE_DIR / "overlay"] + sorted(FEATURE_DIR.glob("adapters/*/overlay"))
    for overlay in overlays:
        assert check_overlay(overlay, ROOTS) == [], overlay
        for path in overlay.rglob("*"):
            if path.is_file() and path.parts[len(overlay.parts)] == "locale":
                assert path.suffix in {".po", ".pot"}, path
    shipped = [p.name for p in (FEATURE_DIR / "overlay/locale").iterdir()]
    assert shipped == ["en.po"], "only English ships; other languages are user requests"


def test_shipped_english_po_is_header_only() -> None:
    text = (FEATURE_DIR / "overlay/locale/en.po").read_text(encoding="utf-8")
    assert '"Language: en\\n"' in text
    assert "Plural-Forms: nplurals=2; plural=(n != 1);" in text
    assert parse_po(text) == []


def test_every_blueprint_has_an_adapter_with_pot_sources() -> None:
    feature = _feature()
    core_overlay = blueprints.BLUEPRINTS_DIR / blueprints.CORE_DIR_NAME / "overlay"
    for blueprint in blueprints.list_blueprints():
        pot = FEATURE_DIR / "adapters" / blueprint.id / "overlay/locale/messages.pot"
        assert pot.is_file(), f"no messages.pot for {blueprint.id}"
        assert {"Play", "Settings", "Back", "Language"} <= _msgids(pot.read_text(encoding="utf-8"))
        assert blueprint.id in feature.adapters, f"no POT source list for {blueprint.id}"
        sources = feature.adapters[blueprint.id].append.get(POT_FILES, [])
        assert sources and sources == sorted(set(sources)), blueprint.id
        for source in sources:
            assert source.startswith(POT_SOURCE_PREFIXES), source
            rel = source.removeprefix("res://")
            candidates = (core_overlay / rel, blueprint.dir / "overlay" / rel, FEATURE_DIR / "overlay" / rel)
            assert any(c.is_file() for c in candidates), f"{blueprint.id}: {source} does not exist"
    extra = set(feature.adapters) - {b.id for b in blueprints.list_blueprints()}
    assert not extra, f"adapters for unknown blueprints: {sorted(extra)}"


def test_translations_feature_passes_catalogue_checks() -> None:
    problems = [p for p in blueprints.validate_catalogue() if "translations" in p.lower()]
    assert problems == []


def test_translations_combine_with_every_blueprint() -> None:
    for blueprint in blueprints.list_blueprints():
        resolution = blueprints.resolve(blueprint.id, [FEATURE])
        assert resolution.problems == [], (blueprint.id, resolution.problems)
        assert [f.id for f in resolution.features] == [FEATURE]


@pytest.mark.parametrize("blueprint", ["blank", "puzzle-cards"])
def test_apply_then_remove_leaves_the_project_as_it_was(tmp_path: Path, blueprint: str) -> None:
    project = make_project(tmp_path, blueprint)
    before = (project / "project.godot").read_text()
    blueprints.add_feature(project, FEATURE)
    text = (project / "project.godot").read_text()
    assert 'Language="*res://features/translations/language.gd"' in text
    assert 'locale/translations=PackedStringArray("res://locale/en.po")' in text
    assert 'locale/fallback="en"' in text
    assert "pseudolocalization/expansion_ratio=0.3" in text
    assert "locale/translations_pot_files=PackedStringArray(" in text
    assert (project / "locale/en.po").is_file()
    assert (project / "locale/messages.pot").read_bytes() == (
        FEATURE_DIR / "adapters" / blueprint / "overlay/locale/messages.pot"
    ).read_bytes()
    assert (project / "features/translations/language.gd").is_file()
    assert (project / "tests/features/translations/translations_test.gd").is_file()
    again = blueprints.plan(project, blueprint, [FEATURE])
    assert again.conflicts == [] and again.changes == []

    removed = blueprints.remove_feature(project, FEATURE)
    assert removed.kept == []
    assert (project / "project.godot").read_text() == before
    for gone in ("locale", "features/translations", "tests/features/translations"):
        assert not (project / gone).exists(), gone


def test_removing_keeps_a_language_the_user_added(tmp_path: Path) -> None:
    project = make_project(tmp_path, "blank", [FEATURE])
    (project / "locale/de.po").write_text(GERMAN_PO)
    blueprints.remove_feature(project, FEATURE)
    assert (project / "locale/de.po").read_text() == GERMAN_PO
    assert not (project / "locale/en.po").exists()


# --- the text collector --------------------------------------------------------


@needs_godot
def test_extract_finds_core_menu_and_scene_text(tmp_path: Path) -> None:
    project = make_project(tmp_path, "blank", [FEATURE])
    out = tmp_path / "messages.pot"
    _extract(project, out)
    entries = parse_po(out.read_text(encoding="utf-8"))
    found = {e.msgid: e for e in entries}
    for msgid in (
        "Play", "Settings", "Quit", "Resume", "Restart", "Main menu", "Paused",
        "Video", "Audio", "Accessibility", "Master volume", "Screen shake", "Back",
        "Language", "Pseudo-locale",
    ):
        assert msgid in found, msgid
    assert found["Play"].refs == ["res://core/menus.gd"]
    assert found["Your game starts here"].refs == ["res://game/level.tscn"]
    for not_text in ("Root", "Toasts", "start", "main", "Title", "Column", "en"):
        assert not_text not in found, not_text
    assert not [m for m in found if m.startswith(("res://", "user://"))]
    assert len(found) == len(entries), "every message id is listed once"


@needs_godot
def test_extract_reads_every_kind_of_text_and_nothing_else(tmp_path: Path) -> None:
    project = make_project(tmp_path, "blank", [FEATURE])
    fixture = project / "game/strings_fixture"
    fixture.mkdir(parents=True)
    (fixture / "strings.gd").write_text(FIXTURE_GD)
    (fixture / "strings.tscn").write_text(FIXTURE_TSCN)
    out = tmp_path / "fixture.pot"
    _extract(project, out, "--dirs=res://game/strings_fixture")
    got = {(e.context, e.msgid, e.plural) for e in parse_po(out.read_text())}
    assert got == FIXTURE_WANTED, {"missing": FIXTURE_WANTED - got, "unexpected": got - FIXTURE_WANTED}


@needs_godot
def test_extract_starts_merges_and_exports_languages(tmp_path: Path) -> None:
    project = make_project(tmp_path, "blank", [FEATURE])
    shipped = (project / "locale/messages.pot").read_text()
    _extract(project, project / "locale/messages.pot", "--add=it")
    italian = project / "locale/it.po"
    text = italian.read_text(encoding="utf-8")
    assert '"Language: it\\n"' in text and "Plural-Forms: nplurals=" in text
    assert _msgids(text) == _msgids((project / "locale/messages.pot").read_text())
    assert _msgids(shipped) >= _msgids(text), "the shipped POT covers the blueprint"

    # Translate one text, drop one from the game, add one, then merge.
    text = text.replace('msgid "Play"\nmsgstr ""', 'msgid "Play"\nmsgstr "Gioca"')
    text = text.replace('msgid "Quit"\nmsgstr ""', 'msgid "Quit"\nmsgstr "Esci"')
    italian.write_text(text)
    menus = project / "core/menus.gd"
    menus.write_text(
        menus.read_text().replace('"Quit"', 'tr("Leave")').replace(
            '"Paused"', 'tr("Game paused")'
        )
    )
    again = _extract(project, project / "locale/messages.pot", "--update-po", "--add=it")
    assert "already exists" in again.output
    merged = italian.read_text(encoding="utf-8")
    entries = {e.msgid: e for e in parse_po(merged)}
    assert entries["Play"].msgstr == ["Gioca"]
    assert entries["Leave"].msgstr == [""]
    assert "Quit" not in entries
    assert '#~ msgid "Quit"\n#~ msgstr "Esci"' in merged, "translations of removed text are kept"
    assert "Paused" not in entries and "Game paused" in entries
    assert '"Language: it\\n"' in merged

    csv = project / "locale/messages.csv"
    _extract(project, project / "locale/messages.pot", f"--csv={csv}")
    rows = csv.read_text(encoding="utf-8").splitlines()
    assert rows[0] == "keys,en,it"
    assert "Play,Play,Gioca" in rows
    italian.unlink()
    _reimport(project)
    assert (project / "locale/messages.it.translation").is_file()
    result = run_godot(
        project, TEST, (_settings(tmp_path), "--expect-locales=it"), timeout=180, marker="translations"
    )
    assert "FAIL" not in result.output


# --- playing in stock Godot ------------------------------------------------------


@needs_godot
def test_language_switch_page_pseudo_locale_and_saved_choice(tmp_path: Path) -> None:
    project = make_project(tmp_path, "blank", [FEATURE])
    (project / "locale/de.po").write_text(GERMAN_PO)
    settings = _settings(tmp_path)
    first = run_godot(
        project, TEST, (settings, "--expect-locales=de"), timeout=180, marker="translations"
    )
    assert "translations: de.po checked" in first.output
    saved = (tmp_path / "settings.cfg").read_text()
    assert 'code="de"' in saved and "pseudo=true" in saved
    second = run_godot(
        project, TEST, (settings, "--translations-phase=verify"), timeout=180, marker="translations"
    )
    assert "translations: saved German checked" in second.output
    assert 'code="en"' in (tmp_path / "settings.cfg").read_text()


@needs_godot
def test_pseudo_locale_argument(tmp_path: Path) -> None:
    project = make_project(tmp_path, "blank", [FEATURE])
    run_godot(
        project,
        TEST,
        (_settings(tmp_path), "--translations-phase=pseudo-arg", "--pseudo-locale"),
        timeout=180,
        marker="translations",
    )
    assert not (tmp_path / "settings.cfg").exists() or "pseudo" not in (
        tmp_path / "settings.cfg"
    ).read_text()


@needs_godot
@pytest.mark.parametrize("blueprint", _blueprint_ids())
def test_every_blueprint_plays_in_another_language(tmp_path: Path, blueprint: str) -> None:
    if blueprints.resolve(blueprint, [FEATURE]).problems:
        pytest.skip(f"{blueprint} does not resolve yet")
    project = make_project(tmp_path, blueprint, [FEATURE])
    run_godot(project, TEST, (_settings(tmp_path),), timeout=180, marker="translations")
    boot(project, frames=120, user_args=(*TEST_ARGS, "--pseudo-locale"))


HUD_CHECK = """extends SceneTree
## Turns the pseudo-locale on mid-game: every HUD text drawn with tr() is drawn
## again in the new language at once, and never translated twice.

const Harness := preload("res://tests/support/play.gd")

var h: Harness


func _initialize() -> void:
	_run.call_deferred()


func _run() -> void:
	h = Harness.new(self, "hud_language")
	await h.boot()
	await h.physics_frames(30)
	var labels: Array[Label] = _hud_labels(root)
	var before: PackedStringArray = PackedStringArray()
	for label: Label in labels:
		before.append(label.text)
	h.expect(not labels.is_empty(), "the HUD shows text")
	var language: Node = root.get_node(^"Language")
	language.call(&"set_pseudo", true)
	await h.process_frames(2)
	for i: int in labels.size():
		var label: Label = labels[i]
		if not is_instance_valid(label) or not label.is_visible_in_tree():
			continue
		var text: String = label.text
		h.expect(text != before[i], "%s is drawn again (%s)" % [label.name, text])
		h.expect(
			text.begins_with("[") and not text.begins_with("[["),
			"%s is translated once (%s)" % [label.name, text]
		)
	language.call(&"set_pseudo", false)
	await h.process_frames(2)
	for i: int in labels.size():
		var label: Label = labels[i]
		if is_instance_valid(label) and label.is_visible_in_tree():
			h.expect(not label.text.begins_with("["), "%s is back in English" % label.name)
	h.finish()


func _hud_labels(node: Node) -> Array[Label]:
	var found: Array[Label] = []
	if node is CanvasItem and not (node as CanvasItem).is_visible_in_tree():
		return found
	var label: Label = node as Label
	if (
		label != null
		and label.auto_translate_mode == Node.AUTO_TRANSLATE_MODE_DISABLED
		and not label.text.is_empty()
		and _in_hud(label)
	):
		found.append(label)
	for child: Node in node.get_children():
		found.append_array(_hud_labels(child))
	return found


func _in_hud(node: Node) -> bool:
	var at: Node = node
	while at != null:
		var script: Script = at.get_script() as Script
		if script != null and script.resource_path.ends_with("/ui/hud.gd"):
			return true
		at = at.get_parent()
	return false
"""


@needs_godot
@pytest.mark.parametrize(
    "blueprint", [b for b in _blueprint_ids() if (blueprints.BLUEPRINTS_DIR / b / "overlay/game/ui/hud.gd").is_file()]
)
def test_hud_follows_a_language_switch(tmp_path: Path, blueprint: str) -> None:
    """HUD numbers are drawn with tr() % n: a live language switch redraws
    them, and the label never translates the result a second time."""
    project = make_project(tmp_path, blueprint, [FEATURE])
    (project / "hud_language_check.gd").write_text(HUD_CHECK, encoding="utf-8")
    run_godot(
        project, "res://hud_language_check.gd", ("--autostart", _settings(tmp_path)),
        timeout=180, marker="hud_language",
    )


@needs_godot
def test_translations_with_every_feature(tmp_path: Path) -> None:
    """Puzzle cards ships with translations by default; add everything else."""
    features = [f.id for f in blueprints.list_features() if f.supports("puzzle-cards")]
    resolution = blueprints.resolve("puzzle-cards", features)
    if resolution.problems:
        pytest.skip("features do not combine yet: " + "; ".join(resolution.problems))
    project = make_project(tmp_path, "puzzle-cards", features)
    run_godot(project, TEST, (_settings(tmp_path),), timeout=180, marker="translations")


# --- the shipped templates --------------------------------------------------------


def _all_features(blueprint: str) -> list[str]:
    """Every feature that combines with `blueprint`, translations included."""
    chosen = [FEATURE]
    for feature in blueprints.list_features():
        if feature.id == FEATURE or not feature.supports(blueprint):
            continue
        if not blueprints.resolve(blueprint, [*chosen, feature.id]).problems:
            chosen.append(feature.id)
    return chosen


def _generated_section(sources: dict[str, list[str]]) -> str:
    lines: list[str] = []
    for blueprint in sorted(sources):
        lines += ["", f"[adapters.{blueprint}.append]", f'"{POT_FILES}" = [']
        lines += [f'\t"{path}",' for path in sources[blueprint]]
        lines.append("]")
    return "\n".join(lines) + "\n"


def _toml_with(section: str) -> str:
    text = (FEATURE_DIR / "feature.toml").read_text(encoding="utf-8")
    start = text.index(GENERATED_MARK)
    end = text.index("\n\n", start) if "\n\n" in text[start:] else len(text)
    return text[:end].rstrip("\n") + "\n" + section


@needs_godot
def test_shipped_pot_files_are_current(tmp_path: Path) -> None:
    """Each blueprint's messages.pot holds every text of the core, the
    blueprint and every feature that combines with it; its POT source list
    names the core, blueprint and translations files with text."""
    stale: list[str] = []
    sources: dict[str, list[str]] = {}
    for blueprint in _blueprint_ids():
        work = tmp_path / blueprint
        work.mkdir()
        project = make_project(work, blueprint, _all_features(blueprint))
        out = work / "messages.pot"
        _extract(project, out)
        text = out.read_text(encoding="utf-8")
        refs = sorted({r for e in parse_po(text) for r in e.refs})
        sources[blueprint] = [r for r in refs if r.startswith(POT_SOURCE_PREFIXES)]
        shipped = FEATURE_DIR / "adapters" / blueprint / "overlay/locale/messages.pot"
        if REFRESH:
            shipped.parent.mkdir(parents=True, exist_ok=True)
            shipped.write_text(text, encoding="utf-8")
        elif not shipped.is_file() or shipped.read_text(encoding="utf-8") != text:
            stale.append(f"{shipped.relative_to(blueprints.FEATURES_DIR)}")
        shutil.rmtree(work)
    toml = _toml_with(_generated_section(sources))
    if REFRESH:
        (FEATURE_DIR / "feature.toml").write_text(toml, encoding="utf-8")
    elif (FEATURE_DIR / "feature.toml").read_text(encoding="utf-8") != toml:
        stale.append("translations/feature.toml (POT source lists)")
    assert not stale, (
        "Game text changed; rebuild with "
        "JOINERY_REFRESH_POT=1 uv run pytest tests/blueprints/test_feature_translations.py -k shipped: "
        + ", ".join(stale)
    )
