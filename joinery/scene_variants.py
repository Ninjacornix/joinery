"""Scene variants: inherited scenes made from a scene, and what each one overrides.

A variant is Godot's own inherited scene: its root is an instance of the base scene
(``[node name="Goblin" instance=ExtResource("1_base")]``) and it stores only what differs:
properties set on the base's nodes, and nodes it adds. Stock Godot opens it as it would
its own "New Inherited Scene"; editing the base changes every variant that doesn't
override that property.

- :func:`make` writes a new variant's text from a base and optional overrides.
- :func:`read` lists a variant's overrides against its base (the base's own base too):
  each overridden property with the base's value and the variant's, each added node, each
  added connection, and the problems Godot would hit (a node the base doesn't have).
- :func:`tool` is the agents' ``make_variant`` (gateway RPC ``make_variant``): it stages
  the new variant in the turn's change.
- :func:`review_items` gives the review a ``variant`` item for every changed inherited
  scene: the overrides as a compact list, and the base and the variant as the picture
  (rendered by the editor, ``review_scene_shot.gd``).

See ADR 0066.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from joinery.blueprints import new_uid
from joinery.scene_text import Scene, quote

if TYPE_CHECKING:
    from joinery.gateway import ProjectGateway

SUFFIX = ".tscn"
#: Where review pictures go in the stage (kept out of the change and Godot's import).
PREVIEWS = ".joinery/previews"
#: Folders a variant is never written into.
_NO_WRITE = ("addons/", ".godot/", ".joinery/", "policy/", ".git/")
_PROPERTY = re.compile(r"^[A-Za-z_][\w/:.]*$")
_NODE_PATH = re.compile(r"^(\.|[^/\"\[\]\n.:@%][^\"\[\]\n:@%]*)$")
#: A Godot value literal: number, bool, null, string, StringName, NodePath, a
#: constructor call, an array or a dictionary.
_LITERAL = re.compile(
    r"^(-?\d+(\.\d+)?(e[-+]?\d+)?|-?inf|nan|true|false|null|\".*\"|&\".*\"|\^\".*\""
    r"|[A-Z]\w*\(.*\)|\[.*\]|\{.*\})$",
    re.DOTALL,
)
#: Overrides listed at most in a review or a tool result.
LIMIT = 40
NEXT = (
    "The variant is staged. Change it further with edit_scene (open it first) or the "
    "scene's own tools, or call submit_for_review; the review lists only what it overrides."
)


class VariantError(ValueError):
    """A variant that can't be made, in words the agent can act on."""


@dataclass
class Override:
    """One property a variant sets on a node of its base."""

    node: str
    property: str
    value: str
    #: The base's value; "" when the base keeps the engine's default.
    base: str = ""
    #: Both values as a person reads them ("at (0, 0.8, 0), ×1.6", "hat.png").
    base_said: str = ""
    said: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "node": self.node,
            "property": self.property,
            "base": self.base,
            "value": self.value,
            "base_said": self.base_said or self.base,
            "said": self.said or self.value,
        }

    def line(self) -> str:
        where = "root" if self.node == "." else self.node
        was = self.base_said or self.base or "default"
        return f"{where} · {self.property}: {_short(was)} → {_short(self.said or self.value)}"


@dataclass
class Variant:
    """A variant read against its base."""

    path: str
    base: str
    overrides: list[Override] = field(default_factory=list)
    #: Nodes the variant adds: {"node", "type"} (type is the scene for an instance).
    added: list[dict[str, str]] = field(default_factory=list)
    #: Connections the variant adds: "signal: from → to.method".
    connections: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    def count(self) -> int:
        return len(self.overrides) + len(self.added) + len(self.connections)

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "base": self.base,
            "overrides": [o.as_dict() for o in self.overrides[:LIMIT]],
            "added": self.added[:LIMIT],
            "connections": self.connections[:LIMIT],
            "count": self.count(),
            "problems": self.problems,
        }

    def fact(self) -> str:
        """One short line for the review's facts ("variant of goblin.tscn, 3 overrides")."""
        parts = []
        if self.overrides:
            parts.append(_plural(len(self.overrides), "override"))
        if self.added:
            parts.append(_plural(len(self.added), "added node"))
        if self.connections:
            parts.append(_plural(len(self.connections), "connection"))
        what = ", ".join(parts) if parts else "same as its base so far"
        return f"variant of {PurePosixPath(self.base.removeprefix('res://')).name}, {what}"


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" + ("" if count == 1 else "s")


_NUMBERS = re.compile(r"-?\d+(?:\.\d+)?(?:e[-+]?\d+)?")


def _num(value: float) -> str:
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return "0" if text in {"-0", ""} else text


def said(value: str, scene: Scene | None = None) -> str:
    """A value as a person reads it: a transform as its place and size, a resource as its
    file; anything else as written."""
    text = value.strip()
    numbers = [float(n) for n in _NUMBERS.findall(text.partition("(")[2])]
    if text.startswith("Transform3D(") and len(numbers) == 12:
        axes = [numbers[0:3], numbers[3:6], numbers[6:9]]
        scales = [sum(c * c for c in axis) ** 0.5 for axis in axes]
        place = "(" + ", ".join(_num(n) for n in numbers[9:12]) + ")"
        return f"at {place}" + _scale(scales) + _turned(axes, scales)
    if text.startswith("Transform2D(") and len(numbers) == 6:
        axes = [numbers[0:2], numbers[2:4]]
        scales = [sum(c * c for c in axis) ** 0.5 for axis in axes]
        place = "(" + ", ".join(_num(n) for n in numbers[4:6]) + ")"
        return f"at {place}" + _scale(scales) + _turned(axes, scales)
    if text.startswith("ExtResource("):
        path = scene.ext_path(text) if scene is not None else ""
        return PurePosixPath(path.removeprefix("res://")).name if path else "a project file"
    if text.startswith("SubResource("):
        section = scene.sub_of(text) if scene is not None else None
        kind = section.attr("type") if section is not None else "resource"
        return f"its own {kind}"
    return text


def _scale(scales: list[float]) -> str:
    if all(abs(s - 1.0) < 1e-4 for s in scales):
        return ""
    if max(scales) - min(scales) < 1e-4:
        return f", ×{_num(scales[0])}"
    return ", ×(" + ", ".join(_num(s) for s in scales) + ")"


def _turned(axes: list[list[float]], scales: list[float]) -> str:
    for index, axis in enumerate(axes):
        unit = [c / (scales[index] or 1.0) for c in axis]
        if abs(abs(unit[index]) - 1.0) > 1e-3:
            return ", turned"
    return ""


def _short(text: str, limit: int = 48) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def relative(path: str) -> str:
    """A project-relative path from ``res://…`` or relative text; refuses escapes."""
    text = str(path).replace("\\", "/").strip()
    text = text.removeprefix("res://").strip("/")
    parts = PurePosixPath(text).parts
    if not text or ".." in parts or text.startswith("/") or ":" in parts[0]:
        raise VariantError(f"Not a project file: {path}")
    return text


def base_of(text: str) -> str:
    """The base scene's ``res://`` path when ``text`` is an inherited scene, else ""."""
    scene = Scene(text)
    for node in scene.nodes():
        if node.path == ".":
            return node.instance
    return ""


def _read(root: Path, res: str) -> str:
    try:
        return (root / relative(res)).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raise VariantError(f"{res} can't be read.") from None


def flatten(root: Path, res: str, seen: tuple[str, ...] = ()) -> dict[str, dict[str, Any]]:
    """Every node of scene ``res`` as {path: {"type", "props"}}, with what it inherits
    and the nodes of the scenes it instances; raises on a scene that inherits itself."""
    if res in seen:
        raise VariantError(
            "A scene can't inherit from itself: " + " → ".join([*seen, res]) + "."
        )
    scene = Scene(_read(root, res))
    nodes: dict[str, dict[str, Any]] = {}
    for node in scene.nodes():
        if node.instance:
            try:
                inner = flatten(root, node.instance, (*seen, res))
            except VariantError:
                if node.path == ".":
                    raise
                inner = {}
            for path, found in inner.items():
                key = node.path if path == "." else (
                    path if node.path == "." else f"{node.path}/{path}"
                )
                entry = nodes.setdefault(key, {"type": found["type"], "props": {}})
                entry["props"].update(found["props"])
            if node.path not in nodes:
                nodes[node.path] = {"type": node.instance, "props": {}}
        entry = nodes.setdefault(node.path, {"type": node.type, "props": {}})
        if node.type:
            entry["type"] = node.type
        entry["props"].update(node.props())
    return nodes


def read(root: Path, rel: str, text: str | None = None) -> Variant | None:
    """The variant at ``rel`` (under ``root``) against its base; None when it isn't one."""
    if text is None:
        text = _read(root, rel)
    scene = Scene(text)
    base = base_of(text)
    if not base:
        return None
    variant = Variant(rel, base)
    try:
        base_nodes = flatten(root, base, ("res://" + relative(rel),))
    except VariantError as exc:
        variant.problems.append(str(exc))
        return variant
    added: set[str] = set()
    for node in scene.nodes():
        new = node.path != "." and (
            node.path not in base_nodes
            and (bool(node.type) or bool(node.instance) or node.parent in added)
        )
        if new:
            added.add(node.path)
            variant.added.append(
                {"node": node.path, "type": node.type or node.instance or "Node"}
            )
            continue
        if node.path not in base_nodes:
            variant.problems.append(
                f"{rel}: node {node.path} isn't in {base}, so Godot can't apply its "
                "properties. Name a node the base has, or give a new node a type."
            )
            continue
        old = base_nodes[node.path]["props"]
        for key, value in node.props().items():
            if node.path == "." and key == "instance":
                continue
            if old.get(key, "").strip() == value.strip():
                continue
            base_value = old.get(key, "").strip()
            variant.overrides.append(
                Override(
                    node.path,
                    key,
                    value.strip(),
                    base_value,
                    said(base_value) if base_value else "",
                    said(value, scene),
                )
            )
    for section in scene.of_kind("connection"):
        target = section.attr("to")
        variant.connections.append(
            f"{section.attr('signal')}: {section.attr('from')} → "
            f"{section.attr('method')}()" + ("" if target == "." else f" on {target}")
        )
    return variant


def _literal(value: Any) -> str:
    """A Godot value literal from the tool's input: JSON numbers and booleans as they
    are, a literal string as written ("Color(1, 0, 0, 1)"), any other text quoted."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(value)
    if not isinstance(value, str):
        raise VariantError("An override value is a Godot value written as text, a number or a bool.")
    text = value.strip()
    if not text or "\n" in text or len(text) > 400:
        raise VariantError("An override value is one line of up to 400 characters.")
    if "ExtResource(" in text or "SubResource(" in text:
        raise VariantError(
            "make_variant sets plain values; change resources (scripts, meshes, "
            "textures) on the staged variant with edit_scene."
        )
    meant = re.match(r"^([A-Z]\w*\(|\[|\{)", text) is not None
    balanced = not text.count('"') % 2 and all(
        text.count(a) == text.count(b) for a, b in ("()", "[]", "{}")
    )
    if meant and not balanced:
        raise VariantError(f"{value} isn't a whole Godot value.")
    if not _LITERAL.match(text) or not balanced:
        return quote(text)
    return text


def _target(root: Path, base: str, path: str) -> str:
    """Where the new variant goes: ``path``, else beside the base, named after it."""
    if path:
        rel = relative(path)
        if not rel.endswith(SUFFIX):
            raise VariantError(f"A variant is a .tscn scene: {path}")
        if (root / rel).exists():
            raise VariantError(f"res://{rel} already exists; pick another path.")
    else:
        stem = base.removesuffix(SUFFIX)
        rel, count = f"{stem}_variant{SUFFIX}", 2
        while (root / rel).exists():
            rel, count = f"{stem}_variant_{count}{SUFFIX}", count + 1
    if rel.startswith(_NO_WRITE) or "/." in "/" + rel:
        raise VariantError(f"Variants aren't written under {rel.split('/')[0]}/.")
    return rel


@dataclass
class Made:
    path: str
    text: str
    variant: Variant


def make(
    root: Path,
    base: str,
    path: str = "",
    overrides: list[dict[str, Any]] | None = None,
) -> Made:
    """The text of a new variant of ``base`` (under ``root``) with ``overrides``:
    [{"node": "." or a path in the base, "property", "value"}]. Raises VariantError."""
    base_rel = relative(base)
    if not base_rel.endswith(SUFFIX):
        raise VariantError(f"Variants are made from .tscn scenes: {base}")
    if not (root / base_rel).is_file():
        raise VariantError(f"There is no scene res://{base_rel}.")
    base_res = "res://" + base_rel
    rel = _target(root, base_rel, path)
    base_text = _read(root, base_res)
    base_scene = Scene(base_text)
    nodes = flatten(root, base_res, ("res://" + rel,))
    root_node = next((n for n in base_scene.nodes() if n.path == "."), None)
    if root_node is None:
        raise VariantError(f"{base_res} has no root node.")
    head = base_scene.sections[0] if base_scene.sections else None
    base_uid = head.attr("uid") if head is not None and head.kind == "gd_scene" else ""
    by_node: dict[str, dict[str, str]] = {}
    problems: list[str] = []
    for raw in overrides or []:
        if not isinstance(raw, dict):
            raise VariantError("Each override is {node, property, value}.")
        node = str(raw.get("node", ".") or ".").strip().removeprefix("./") or "."
        prop = str(raw.get("property", "")).strip()
        if not _NODE_PATH.match(node):
            raise VariantError(f"{node!r} isn't a node path in the base (use '.' for its root).")
        if not _PROPERTY.match(prop) or prop == "script":
            raise VariantError(f"{prop!r} isn't a property make_variant can set.")
        if node not in nodes:
            known = ", ".join(sorted(p for p in nodes if p != ".")[:12]) or "only the root"
            problems.append(f"{base_res} has no node {node} (it has: {known}).")
            continue
        by_node.setdefault(node, {})[prop] = _literal(raw.get("value"))
    if problems:
        raise VariantError(" ".join(problems))
    ext = '[ext_resource type="PackedScene"'
    if base_uid:
        ext += f" uid={quote(base_uid)}"
    ext += f' path={quote(base_res)} id="1_base"]'
    lines = [f"[gd_scene format=3 uid={quote(new_uid())}]", "", ext, ""]
    lines.append(f'[node name={quote(root_node.name)} instance=ExtResource("1_base")]')
    lines += [f"{k} = {v}" for k, v in by_node.pop(".", {}).items()]
    order = [n.path for n in base_scene.nodes()] + sorted(nodes)
    for node in dict.fromkeys(order):
        if node not in by_node:
            continue
        parent, _, name = node.rpartition("/")
        lines += ["", f"[node name={quote(name)} parent={quote(parent or '.')}]"]
        lines += [f"{k} = {v}" for k, v in by_node[node].items()]
    text = "\n".join(lines) + "\n"
    variant = read(root, rel, text)
    assert variant is not None
    return Made(rel, text, variant)


def check(root: Path, changed: list[str]) -> list[str]:
    """Problems of the changed inherited scenes under ``root`` (the stage): a missing
    base, a scene that inherits itself, or a property on a node the base lacks."""
    found: list[str] = []
    for rel in changed:
        if not rel.endswith(SUFFIX) or not (root / rel).is_file():
            continue
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        base = base_of(text)
        if not base:
            continue
        if not base.startswith("res://") or not (root / relative(base)).is_file():
            found.append(f"{rel}: its base scene {base} doesn't exist.")
            continue
        variant = read(root, rel, text)
        if variant is not None:
            found += [p if p.startswith(rel) else f"{rel}: {p}" for p in variant.problems]
    return found


def review_items(
    stage: Path, original: Path, changes: list[dict[str, str]]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Review items for the changed inherited scenes, and a fact for each.

    Each item is ``{path, media: "image", label: "Variant", variant: {...}, before,
    after, scenes: {before, after}}``: the editor renders ``scenes`` (the base, or the
    variant as it was, and the staged variant) into ``before`` and ``after``."""
    items: list[dict[str, Any]] = []
    facts: list[str] = []
    previews = stage / PREVIEWS
    for change in changes:
        rel = change["path"]
        if not rel.endswith(SUFFIX) or not change["after"] or len(rel) > 300:
            continue
        try:
            variant = read(stage, rel)
        except (VariantError, OSError, ValueError):
            continue
        if variant is None:
            continue
        stem = re.sub(r"[^A-Za-z0-9_-]+", "-", rel.removesuffix(SUFFIX)).strip("-")
        previews.mkdir(parents=True, exist_ok=True)
        before_scene = (
            str(original / rel) if change["before"] and (original / rel).is_file()
            else variant.base
        )
        items.append(
            {
                "path": rel,
                "media": "image",
                "label": "Variant",
                "variant": variant.as_dict(),
                "before": str(previews / f"variant-{stem}-before.png"),
                "after": str(previews / f"variant-{stem}-after.png"),
                "scenes": {"before": before_scene, "after": str(stage / rel)},
            }
        )
        facts.append(variant.fact())
    return items, facts


async def tool(gateway: ProjectGateway, params: dict[str, Any]) -> dict[str, Any]:
    """``make_variant``: stage a new inherited scene of ``base`` in the turn's change."""
    if gateway.turn is not None:
        gateway.turn.admit_mutation()
    base = params.get("base")
    if not isinstance(base, str) or not base.strip():
        raise TypeError("base is the res:// scene to make a variant of.")
    path = params.get("path") or ""
    if not isinstance(path, str):
        raise TypeError("path is the res:// path of the new variant.")
    overrides = params.get("overrides") or []
    if not isinstance(overrides, list) or len(overrides) > LIMIT:
        raise ValueError(f"overrides is a list of up to {LIMIT} {{node, property, value}}.")
    async with gateway.call_lock:
        tx = gateway.staging()
        try:
            made = make(tx.stage, base, path, overrides)
        except VariantError as exc:
            return {"status": "problems", "problems": [str(exc)]}
        tx.note_tool("make_variant")
        target = tx.stage / made.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(made.text, encoding="utf-8")
        if gateway.claims.contended():
            gateway.claim_stage(strict=True)
    variant = made.variant
    return {
        "status": "staged",
        "path": "res://" + made.path,
        "base": variant.base,
        "overrides": [o.line() for o in variant.overrides],
        "facts": [variant.fact()],
        "next": NEXT,
    }
