"""What the game never uses inside its files: input actions, autoloads, nodes that do
nothing, and attached scripts that add nothing; and the text edits that take each out.

* An **input action** no script, scene, resource or design file names (Godot's own
  ``ui_*`` actions are never findings). A name a script builds (``"move_" + dir``) or
  a script that lists every action (a controls page) makes it "maybe used".
* An **autoload** nothing names (as ``Name``, ``"Name"`` or ``/root/Name``) whose
  script has no engine callback, so it never runs by itself; or one whose file is gone.
* A **node** in a scene the game uses that does nothing: hidden and only drawing
  (meshes, sprites, labels, lights, particles), never shown by code; disabled and not
  drawn; or an empty ``Node``/``Node2D``/``Node3D`` with no children, script, groups or
  metadata. Its name appearing in any script or NodePath keeps it. A scene whose
  scripts reach nodes by a name they build (``get_node(name)``, ``find_child``) makes it
  "maybe used".
* An **empty script**: only ``extends <engine class>`` (no comment, no code), attached
  to nodes. Its removal detaches it and deletes the file.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import PurePosixPath

from joinery.godot_config import GodotConfig
from joinery.scene_text import Scene, Section
from joinery.tidy import (
    CALLBACKS,
    CODE,
    EVERY_ACTION,
    NOT_GAME,
    Finding,
    Graph,
    finding_id,
    kept,
    sidecars,
    size_of,
    strings,
    suffix,
)

#: Types that only draw: hidden, they do nothing.
VISUAL = re.compile(
    r"^(?:MeshInstance[23]D|MultiMeshInstance[23]D|Sprite[23]D|AnimatedSprite[23]D|"
    r"Label|Label3D|RichTextLabel|Polygon2D|Line2D|Decal|TextureRect|ColorRect|"
    r"NinePatchRect|Panel|PanelContainer|[HV]BoxContainer|MarginContainer|"
    r"CenterContainer|GridContainer|Control|Node2D|Node3D|OmniLight3D|SpotLight3D|"
    r"DirectionalLight3D|PointLight2D|GPUParticles[23]D|CPUParticles[23]D|"
    r"Marker[23]D)$"
)
#: Types that, disabled, neither draw nor act.
INERT = re.compile(
    r"^(?:Node|Timer|AudioStreamPlayer(?:[23]D)?|AnimationPlayer|AnimationTree|"
    r"Area[23]D|StaticBody[23]D|CharacterBody[23]D|RigidBody[23]D|AnimatableBody[23]D|"
    r"CollisionShape[23]D|CollisionPolygon[23]D|RayCast[23]D|ShapeCast[23]D|"
    r"NavigationAgent[23]D)$"
)
EMPTY_TYPES = frozenset({"Node", "Node2D", "Node3D"})
#: A script reaching nodes by a name it builds.
DYNAMIC_NODES = re.compile(
    r"\bget_children\s*\(|\bfind_child(?:ren)?\s*\(|\bget_child\s*\(|"
    r"\bget_node(?:_or_null)?\s*\(\s*[^\"'^)\s]"
)
_ACTION = re.compile(r"^[A-Za-z_][\w.]*$")


# --- searching the game's text -------------------------------------------------------


class Corpus:
    """The game's text, gathered once: code, scenes without their node headers, and
    every quoted string with its place."""

    def __init__(self, graph: Graph) -> None:
        self.graph = graph
        code: list[str] = []
        scenes: list[str] = []
        self.literals: list[tuple[str, str, int, str]] = []
        for rel in graph.refs:
            if rel.startswith("addons/") or rel == "project.godot":
                continue
            text = graph.text(rel)
            kind = suffix(rel)
            if kind in CODE:
                code.append(text)
            elif kind in {".tscn", ".tres"}:
                scenes.append(
                    "\n".join(
                        line
                        for line in text.splitlines()
                        if not line.startswith("[node ")
                    )
                )
            else:
                continue
            lines = text.splitlines()
            for number, value in strings(text):
                line = lines[number - 1] if number <= len(lines) else ""
                self.literals.append((value, rel, number, line))
        self.code = "\n".join(code)
        self.scenes = "\n".join(scenes)
        self.values = {value for value, *_ in self.literals}

    def names_node(self, name: str) -> bool:
        """Does any script, NodePath or connection name a node called ``name``?"""
        pattern = re.compile(
            r"(?:\$|%|[\"'/^])" + re.escape(name) + r"(?=[\"'/:.\s)\],]|$)",
            re.MULTILINE,
        )
        return bool(pattern.search(self.code) or pattern.search(self.scenes))

    def built(self, name: str) -> tuple[str, str] | None:
        """(where, line) of a string a script builds ``name`` from, if any."""
        for value, rel, number, line in self.literals:
            if suffix(rel) not in CODE or not value or value == name:
                continue
            if "%" in value:
                pattern = re.escape(value)
                pattern = re.sub(r"%[sd]", ".+", pattern.replace(r"\%", "%"))
                if re.fullmatch(pattern, name):
                    return f"{rel}:{number}", line.strip()[:160]
            elif (
                len(value) >= 2
                and (name.startswith(value) or name.endswith(value))
                and "+" in line
            ):
                return f"{rel}:{number}", line.strip()[:160]
        return None


def _design_words(graph: Graph) -> set[str]:
    words: set[str] = set()
    for rel in graph.files:
        if rel.startswith("design/") and suffix(rel) in {".md", ".txt", ".toml"}:
            words |= set(re.findall(r"[A-Za-z_][\w.]*", graph.text(rel)))
    return words


# --- input actions and autoloads ------------------------------------------------------


def _config(graph: Graph) -> GodotConfig | None:
    if "project.godot" not in graph.present:
        return None
    try:
        return GodotConfig.parse(graph.text("project.godot"))
    except (ValueError, IndexError):
        return None


def input_findings(
    graph: Graph, corpus: Corpus, keep: list[str], final: list[str]
) -> tuple[list[Finding], list[str]]:
    """Actions nothing names; and the keys of those kept."""
    config = _config(graph)
    if config is None:
        return [], []
    design = _design_words(graph)
    every = [
        (rel, n)
        for rel in graph.refs
        if suffix(rel) in CODE and not rel.startswith("addons/")
        for n, line in enumerate(graph.text(rel).splitlines(), 1)
        if EVERY_ACTION.search(line)
    ]
    found: list[Finding] = []
    held: list[str] = []
    for action in config.keys("input"):
        if action.startswith("ui_") or not _ACTION.match(action):
            continue
        key = f"input:{action}"
        if action in corpus.values or action in design:
            continue
        if kept(key, key, keep, final):
            held.append(key)
            continue
        built = corpus.built(action)
        evidence = [
            (
                "Searched every script, scene, resource and design file for "
                f'"{action}" and &"{action}".'
            )
        ]
        confidence = "unused"
        why = (
            f"No script, scene or design file reads the {action} action, so pressing "
            "its keys does nothing."
        )
        if built is not None:
            confidence = "maybe"
            evidence.append(f"{built[0]} builds an action name: {built[1]}")
            why = f"Nothing names {action}, but a script builds action names."
        elif every:
            confidence = "maybe"
            rel, number = every[0]
            evidence.append(f"{rel}:{number} lists every input action")
            why = (
                f"Nothing reads {action}; a script lists every action (a controls "
                "page), so players may still see it there."
            )
        found.append(
            Finding(
                id=finding_id("input", action),
                key=key,
                kind="input",
                path="project.godot",
                subject=action,
                title=f"{action} input action",
                confidence=confidence,  # type: ignore[arg-type]
                size=0,
                why=why,
                evidence=evidence,
                edits=[f"project.godot: the {action} action and its bindings"],
            )
        )
    return found, held


def autoload_targets(graph: Graph) -> dict[str, str]:
    config = _config(graph)
    if config is None:
        return {}
    found: dict[str, str] = {}
    for name in config.keys("autoload"):
        raw = (config.get("autoload", name) or "").strip().strip('"').lstrip("*")
        found[name] = raw.removeprefix("res://")
    return found


def autoload_findings(
    graph: Graph, corpus: Corpus, keep: list[str], final: list[str]
) -> tuple[list[Finding], list[str]]:
    found: list[Finding] = []
    held: list[str] = []
    for name, path in autoload_targets(graph).items():
        key = f"autoload:{name}"
        missing = path not in graph.present
        if not missing and path.startswith(graph.owned):
            continue  # a feature's or the core's own: Game features decides
        if not missing:
            if suffix(path) not in CODE:
                continue
            own = graph.text(path)
            if CALLBACKS.search(own):
                continue
            pattern = re.compile(
                r"(?<![\w.])" + re.escape(name) + r"\b|/root/" + re.escape(name)
            )
            others = "\n".join(
                graph.text(rel)
                for rel in graph.refs
                if rel not in {path, "project.godot"}
                and suffix(rel) in CODE | {".tscn", ".tres"}
            )
            if pattern.search(others):
                continue
        if kept(key, key, keep, final):
            held.append(key)
            continue
        alone = not missing and graph.referrers.get(path, set()) <= {"project.godot"}
        size = (
            sum(size_of(graph.project, p) for p in [path, *sidecars(graph, path)])
            if alone
            else 0
        )
        found.append(
            Finding(
                id=finding_id("autoload", name),
                key=key,
                kind="autoload",
                path="project.godot",
                subject=name,
                title=f"{name} autoload",
                confidence="unused",
                size=size,
                why=(
                    f"The {name} autoload points at res://{path}, which is gone."
                    if missing
                    else f"Nothing calls {name}, and its script has no _ready, "
                    "_process or other callback, so it never runs."
                ),
                evidence=[
                    f"Searched every script and scene for {name} and /root/{name}."
                ],
                edits=[f"project.godot: the {name} autoload"],
            )
        )
    return found, held


def remove_actions(text: str, names: Iterable[str]) -> str:
    config = GodotConfig.parse(text)
    for name in names:
        config.erase("input", name)
    if config.has_section("input") and not config.keys("input"):
        config.remove_section("input")
    return config.dump()


def remove_autoloads(text: str, names: Iterable[str]) -> str:
    config = GodotConfig.parse(text)
    for name in names:
        config.erase("autoload", name)
    if config.has_section("autoload") and not config.keys("autoload"):
        config.remove_section("autoload")
    return config.dump()


# --- nodes ------------------------------------------------------------------------------


def _scripts_of(scene: Scene) -> list[str]:
    paths = []
    for node in scene.nodes():
        script = scene.script_of(node)
        if script:
            paths.append(script.removeprefix("res://"))
    return paths


def _dead(scene: Scene, corpus: Corpus) -> list[tuple[str, str, str]]:
    """(node path, title, why) of the nodes that do nothing, topmost only."""
    nodes = scene.nodes()
    children: dict[str, list[str]] = {}
    by_path = {n.path: n for n in nodes}
    for node in nodes:
        if node.path != ".":
            children.setdefault(node.parent or ".", []).append(node.path)

    def subtree(path: str) -> list[str]:
        out = [path]
        for child in children.get(path, []):
            out += subtree(child)
        return out

    def quiet(path: str) -> bool:
        """No script, instance, groups or metadata; not named anywhere."""
        node = by_path[path]
        props = node.props()
        return (
            not node.instance
            and not node.groups
            and "script" not in props
            and not any(k.startswith("metadata/") for k in props)
            and props.get("unique_name_in_owner") != "true"
            and not corpus.names_node(node.name)
        )

    found: list[tuple[str, str, str]] = []
    taken: set[str] = set()
    for node in nodes:
        if node.path == "." or not node.type:
            continue
        if any(node.path.startswith(t + "/") for t in taken):
            continue
        props = node.props()
        tree = subtree(node.path)
        kinds = [by_path[p].type for p in tree]
        if not all(quiet(p) for p in tree):
            continue
        if props.get("visible") == "false" and all(
            k and VISUAL.match(k) for k in kinds
        ):
            what = "hidden " + node.type
            why = (
                f"{node.name} is hidden and only draws; no script, animation or "
                "NodePath names it, so it is never shown."
            )
        elif (
            props.get("process_mode") == "4"
            and all(k and INERT.match(k) for k in kinds)
            and not any(
                by_path[p].props().get("process_mode") not in {None, "4"}
                for p in tree[1:]
            )
        ):
            what = "disabled " + node.type
            why = (
                f"{node.name} is disabled and draws nothing; nothing turns it back on."
            )
        elif (
            node.type in EMPTY_TYPES
            and len(tree) == 1
            and not any(
                k
                for k in props
                if k not in {"transform", "position", "rotation", "scale", "visible"}
            )
        ):
            what = "empty " + node.type
            why = f"{node.name} has no children, script, groups or settings."
        else:
            continue
        taken.add(node.path)
        extra = f" and {len(tree) - 1} inside it" if len(tree) > 1 else ""
        found.append((node.path, f"{what} {node.name}{extra}", why))
    return found


def node_findings(
    graph: Graph, corpus: Corpus, keep: list[str], final: list[str]
) -> tuple[list[Finding], list[str]]:
    found: list[Finding] = []
    held: list[str] = []
    for rel in sorted(graph.used):
        if suffix(rel) != ".tscn" or rel.startswith(NOT_GAME + graph.owned):
            continue
        scene = Scene(graph.text(rel))
        dead = _dead(scene, corpus)
        if not dead:
            continue
        dynamic = next(
            (
                (script, n, line.strip())
                for script in _scripts_of(scene)
                if script in graph.present
                for n, line in enumerate(graph.text(script).splitlines(), 1)
                if DYNAMIC_NODES.search(line)
            ),
            None,
        )
        for path, title, why in dead:
            key = f"{rel}::{path}"
            if kept(key, key, keep, final):
                held.append(key)
                continue
            name = PurePosixPath(path).name
            evidence = [
                (
                    f'Searched every script and scene for ${name}, %{name}, "{name}" '
                    "and NodePaths through it."
                )
            ]
            if dynamic is not None:
                evidence.append(
                    f"{dynamic[0]}:{dynamic[1]} reaches nodes by a built name: "
                    f"{dynamic[2][:120]}"
                )
            found.append(
                Finding(
                    id=finding_id("node", rel, path),
                    key=key,
                    kind="node",
                    path=rel,
                    subject=path,
                    title=title[:1].upper()
                    + title[1:]
                    + f" in {PurePosixPath(rel).name}",
                    confidence="maybe" if dynamic is not None else "unused",
                    size=0,
                    why=why,
                    evidence=evidence,
                    edits=[f"{rel}: the {path} node"],
                )
            )
    return found, held


def _pruned(scene: Scene) -> None:
    """Drop resources nothing in the scene uses any more."""
    while True:
        body = "\n".join(
            line
            for s in scene.sections
            if s.kind not in {"ext_resource", "sub_resource"}
            for line in s.lines
        )
        subs = "\n".join(
            line for s in scene.of_kind("sub_resource") for line in s.lines[1:]
        )
        exts = "\n".join(
            line for s in scene.of_kind("ext_resource") for line in s.lines[1:]
        )
        used = body + "\n" + subs + "\n" + exts
        drop: list[Section] = []
        for section in scene.sections:
            if section.kind == "ext_resource":
                mark = f'ExtResource("{section.attr("id")}")'
            elif section.kind == "sub_resource":
                mark = f'SubResource("{section.attr("id")}")'
            else:
                continue
            if mark not in used:
                drop.append(section)
        if not drop:
            break
        for section in drop:
            scene.sections.remove(section)
    head = scene.sections[0] if scene.sections else None
    if head is not None and "load_steps" in head.attrs:
        steps = (
            len(scene.of_kind("ext_resource")) + len(scene.of_kind("sub_resource")) + 1
        )
        head.attrs["load_steps"] = str(steps)
        head.lines[0] = re.sub(r"load_steps=\d+", f"load_steps={steps}", head.lines[0])
        if steps <= 1:
            head.lines[0] = re.sub(r"\s*load_steps=\d+", "", head.lines[0])


def _render(scene: Scene) -> str:
    lines = list(scene.preamble)
    for index, section in enumerate(scene.sections):
        body = list(section.lines)
        while len(body) > 1 and not body[-1].strip():
            body.pop()
        following = (
            scene.sections[index + 1] if index + 1 < len(scene.sections) else None
        )
        # Godot writes external resources as one block, other sections apart.
        together = (
            following is not None and section.kind == following.kind == "ext_resource"
        )
        lines += body if together else body + [""]
    return "\n".join(lines).rstrip("\n") + "\n"


def remove_nodes(text: str, paths: Iterable[str]) -> str:
    """The scene without the nodes at ``paths`` (and what is inside them), their
    connections and the resources only they used."""
    scene = Scene(text)
    gone = set(paths)
    for node in scene.nodes():
        if node.path in gone or any(node.path.startswith(p + "/") for p in gone):
            scene.sections.remove(node.section)

    def inside(path: str) -> bool:
        return path in gone or any(path.startswith(p + "/") for p in gone)

    for section in list(scene.sections):
        if (
            section.kind == "connection"
            and (inside(section.attr("from")) or inside(section.attr("to")))
            or section.kind == "editable"
            and inside(section.attr("path"))
        ):
            scene.sections.remove(section)
    _pruned(scene)
    return _render(scene)


def detach_script(text: str, script: str) -> str:
    """The scene or resource with ``script`` taken off every node that has it."""
    scene = Scene(text)
    ids = {
        s.attr("id")
        for s in scene.of_kind("ext_resource")
        if s.attr("path").removeprefix("res://") == script
    }
    for section in scene.sections:
        if section.kind not in {"node", "resource", "sub_resource"}:
            continue
        kept_lines = [
            line
            for line in section.lines
            if not any(line.strip() == f'script = ExtResource("{i}")' for i in ids)
        ]
        section.lines = kept_lines
    _pruned(scene)
    return _render(scene)


# --- empty scripts ------------------------------------------------------------------------


_EXTENDS = re.compile(r"^extends\s+([A-Za-z_]\w*)\s*$")


def empty_script(graph: Graph, rel: str) -> str:
    """The engine class an empty script extends, or "" when it isn't empty."""
    if suffix(rel) != ".gd":
        return ""
    base = ""
    for raw in graph.text(rel).splitlines():
        line = raw.strip()
        if not line:
            continue
        match = _EXTENDS.match(line)
        if match:
            base = match.group(1)
        elif line.startswith(("@tool", "@icon")):
            continue
        else:
            return ""  # code, a comment or a class_name: someone meant it
    if not base or base in graph.classes:
        return ""
    return base


def script_findings(
    graph: Graph, keep: list[str], final: list[str], autoloads: set[str]
) -> tuple[list[Finding], list[str]]:
    found: list[Finding] = []
    held: list[str] = []
    for rel in sorted(graph.used):
        if rel.startswith(NOT_GAME + graph.owned) or rel in autoloads:
            continue
        base = empty_script(graph, rel)
        if not base:
            continue
        users = sorted(graph.referrers.get(rel, set()))
        if not users or any(suffix(u) not in {".tscn", ".tres"} for u in users):
            continue
        if any(u.startswith(NOT_GAME) for u in users):
            continue
        key = f"{rel}::empty"
        if kept(key, rel, keep, final):
            held.append(key)
            continue
        found.append(
            Finding(
                id=finding_id("empty", rel),
                key=key,
                kind="script",
                path=rel,
                subject="empty",
                title=f"Empty script {PurePosixPath(rel).name}",
                confidence="unused",
                size=sum(
                    size_of(graph.project, p) for p in [rel, *sidecars(graph, rel)]
                ),
                why=(
                    f"It only says extends {base}: the nodes it is attached to behave "
                    "the same without it."
                ),
                evidence=[f"Attached in {', '.join(users)}."],
                edits=[f"{u}: take the script off" for u in users],
            )
        )
    return found, held
