"""The design check's deterministic checks (`joinery.design`).

Each check reads the loaded project and returns findings with evidence. None of
them needs a model or a running game; the blockout walk reuses the blockout's own
reachability check (ADR 0043) and the playtest findings read the newest report the
playtest bot wrote (ADR 0038).
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from pathlib import Path

from joinery import brief as brief_mod
from joinery import gdd
from joinery.design import (
    Evidence,
    Finding,
    Project,
    Section,
    Text,
    brief_section,
    code_part,
    make,
    sections,
    strings,
)

Check = Callable[[Project], list[Finding]]

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_BACKTICK = re.compile(r"`([^`]+)`")
#: Input actions no verb needs to read: menus and pause are the core's.
_CORE_ACTIONS = ("ui_", "pause", "report_bug")
#: Actions that run the session rather than play the game; showing them is optional.
_SESSION_ACTIONS = (
    "restart",
    "retry",
    "quit",
    "menu",
    "pause",
    "screenshot",
    "fullscreen",
)


def _gameplay_actions(project: Project) -> list[str]:
    return [a for a in project.actions if not a.startswith(_CORE_ACTIONS)]


def _bound(events: dict[str, list[str]]) -> bool:
    return any(events.get(kind) for kind in ("keys", "mouse", "pad"))


def _action_line(project: Project, action: str) -> int:
    if project.godot is None:
        return 0
    return project.godot.find(rf"^{re.escape(action)}\s*=")


def _input_line(project: Project) -> int:
    return project.godot.find(r"^\[input\]") if project.godot is not None else 0


def _read_by_code(project: Project, action: str) -> bool:
    """Some script or scene names ``action`` as a string (Input.is_action_pressed("jump"))."""
    literal = re.compile(r"(?<![A-Za-z0-9_])[&]?[\"']" + re.escape(action) + r"[\"']")
    return any(literal.search(source.text) for source in project.code().values())


def _brief_ev(section: Section | None, line: int, quote: str = "") -> list[Evidence]:
    if section is None:
        return []
    return [
        Evidence(path=brief_mod.BRIEF_PATH.as_posix(), line=line, quote=quote[:160])
    ]


# --- 1. verbs the brief names: bound, and read by the game -------------------------


def _verb_rows(section: Section) -> list[tuple[int, str, str]]:
    """(line, verb, input-action cell) for each row of the brief's verbs table."""
    rows: list[tuple[int, str, str]] = []
    for line, text in section.body:
        stripped = text.strip()
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) < 2 or set(cells[0]) <= set("-: ") or cells[0].lower() == "verb":
            continue
        rows.append((line, cells[0], cells[1]))
    return rows


def _actions_in(cell: str, known: dict[str, dict[str, list[str]]]) -> list[str]:
    quoted = _BACKTICK.findall(cell)
    if quoted:
        return [name.strip() for name in quoted if name.strip()]
    return [
        word
        for word in _IDENT.findall(cell)
        if word in known or brief_mod._ACTION_NAME.match(word)
    ]


def verbs(project: Project) -> list[Finding]:
    """A core verb in the brief with no input action, no binding, or no code that reads it."""
    section = brief_section(project, "Player verbs")
    if section is None or project.godot is None:
        return []
    found: list[Finding] = []
    for line, verb, cell in _verb_rows(section):
        verb_name = verb.strip("*_ ")
        quote = f"{verb} | {cell}"
        if brief_mod.UNSET in cell or not cell.strip("—–- "):
            found.append(
                make(
                    "unbound-verb",
                    "high",
                    f"{verb_name} has no input action",
                    f"The player has no way to {verb_name.lower()}.",
                    f"The brief lists {verb_name} as a player verb but names no input "
                    "action for it.",
                    _brief_ev(section, line, quote),
                    f"Add an input action for {verb_name} to project.godot with keys "
                    "(and a gamepad button if the brief lists gamepad), name it in the "
                    "brief's verbs table, and make the player script read it.",
                )
            )
            continue
        for action in _actions_in(cell, project.actions):
            if action.startswith("ui_") and action not in project.actions:
                continue  # Godot's own ui_* actions: bound by the engine, read by its controls
            events = project.actions.get(action)
            where = _brief_ev(section, line, quote)
            if events is None:
                found.append(
                    make(
                        "unbound-verb",
                        "high",
                        f"{verb_name}: `{action}` isn't in the input map",
                        f"Pressing anything never makes the player {verb_name.lower()}.",
                        f"The brief binds {verb_name} to `{action}`, but project.godot "
                        "has no such input action.",
                        where
                        + [
                            Evidence(
                                path="project.godot",
                                line=_input_line(project),
                                node="[input]",
                            )
                        ],
                        f"Add the `{action}` input action to project.godot with keys the "
                        "brief's controls table lists, or correct the action name in the "
                        "brief.",
                        key=action,
                    )
                )
            elif not _bound(events):
                found.append(
                    make(
                        "unbound-verb",
                        "high",
                        f"{verb_name}: `{action}` has no key or button",
                        f"No key, mouse button or gamepad button makes the player {verb_name.lower()}.",
                        f"`{action}` is in the input map with no events, and the brief "
                        f"lists {verb_name} as a core verb.",
                        where
                        + [
                            Evidence(
                                path="project.godot",
                                line=_action_line(project, action),
                                node=action,
                            )
                        ],
                        f"Bind `{action}` in project.godot to the keys and buttons the "
                        "brief's controls table lists.",
                        key=action,
                    )
                )
            elif not _read_by_code(project, action):
                found.append(
                    make(
                        "unused-verb",
                        "medium",
                        f"{verb_name} is bound but nothing reads `{action}`",
                        f"Pressing {', '.join(events.get('keys', [])[:2]) or 'its key'} does nothing.",
                        f"The brief lists {verb_name} as a player verb; `{action}` is bound "
                        "in project.godot, but no script or scene reads it.",
                        where
                        + [
                            Evidence(
                                path="project.godot",
                                line=_action_line(project, action),
                                node=action,
                            )
                        ],
                        f"Make the player read `{action}` and do what the brief's verbs "
                        f"table says {verb_name} does, with the feedback it lists.",
                        key=action,
                    )
                )
    return found


# --- 2. mechanics a design document describes that the game never has -------------

_TERM = re.compile(
    r"^\s*(?:[-*+]|\d+[.)])\s+(?:\*\*|__)?([A-Za-z][A-Za-z' -]{1,40}?)(?:\*\*|__)?\s*(?::|—|–| - |\(|$)"
)


def _term_forms(term: str) -> list[str]:
    words = [w for w in re.split(r"[\s'-]+", term.lower()) if w]
    joined = "".join(words)
    return list(dict.fromkeys(["_".join(words), joined, " ".join(words)]))


def _in_game(project: Project, term: str) -> bool:
    forms = _term_forms(term)
    for action in project.actions:
        if any(form.replace(" ", "_") in action.lower() for form in forms):
            return True
    for source in project.code().values():
        lower = source.text.lower()
        if any(re.search(rf"(?<![a-z]){re.escape(form)}", lower) for form in forms):
            return True
    return False


def mechanics(project: Project) -> list[Finding]:
    """A mechanic the design document lists (its verbs or mechanics section) that no
    input action, script or scene ever mentions: a dead end in the design."""
    found: list[Finding] = []
    for doc in project.docs:
        for section in sections(doc):
            if gdd.brief_section(section.heading) != "Player verbs":
                continue
            for line, text in section.body:
                match = _TERM.match(text)
                if match is None:
                    continue
                term = match.group(1).strip(" -'")
                if len(term.split()) > 3 or len(term) < 3:
                    continue
                if _in_game(project, term):
                    continue
                found.append(
                    make(
                        "missing-mechanic",
                        "medium",
                        f"{term.capitalize()} from the design document isn't in the game",
                        f"A player who read the design would look for {term.lower()} and "
                        "never find it.",
                        f'"{section.heading}" in {doc.path} lists {term}, but no input '
                        "action, script or scene mentions it.",
                        [Evidence(path=doc.path, line=line, quote=text.strip()[:160])],
                        f"Implement {term} as the design document describes (an input "
                        "action if it is something the player does, and the code), or "
                        "tell me to take it out of the document.",
                        key=term.lower(),
                    )
                )
    return found


# --- 3. a win or lose state the brief promises, and whether anything reaches it ----

_WIN = re.compile(
    r"\b(?:level_complete[d]?|complete_level|finish_level|level_finished|game_won|"
    r"player_won|you_win|show_win|win_screen|victory)\b",
    re.IGNORECASE,
)
_LOSE = re.compile(
    r"\b(?:level_failed|fail_level|game_over|gameover|player_died|you_lose|you_died|"
    r"show_game_over|lose_screen|defeat)\b",
    re.IGNORECASE,
)
_FUNC = re.compile(r"^\s*(?:static\s+)?func\s+([A-Za-z_]\w*)")
#: Engine callbacks: Godot calls them, so code in them is reached.
_CALLBACKS = frozenset(
    {
        "_ready",
        "_process",
        "_physics_process",
        "_input",
        "_unhandled_input",
        "_unhandled_key_input",
        "_shortcut_input",
        "_gui_input",
        "_notification",
        "_enter_tree",
        "_exit_tree",
        "_integrate_forces",
        "_init",
    }
)
_GOAL_WORDS = {
    "win": ("Win", "win", _WIN, "level_completed"),
    "lose": ("Lose", "lose", _LOSE, "level_failed"),
}


def _promise(text: str, word: str) -> tuple[str, str] | None:
    """The brief's "Win: …" or "Lose: …" words, unless they say there is none."""
    names = "win|winning" if word == "win" else "lose|losing|fail|failure"
    match = re.search(rf"(?im)\b(?:{names})\s*[:–—-]\s*([^.;\n]+)", text)
    if match is None:
        return None
    said = match.group(1).strip()
    if re.match(r"(?i)^(none|no\b|nothing|n/a|-|_?not decided)", said):
        return None
    return match.group(0).strip(), said


def _emitters(
    project: Project, pattern: re.Pattern[str]
) -> list[tuple[Text, int, str]]:
    """(script, line, function) where code ends the game this way (not declarations)."""
    found: list[tuple[Text, int, str]] = []
    for source in project.scripts.values():
        function = ""
        for index, raw in enumerate(source.lines, start=1):
            named = _FUNC.match(raw)
            if named:
                function = named.group(1)
            line = code_part(raw)
            if not pattern.search(line):
                continue
            stripped = line.strip()
            if (
                stripped.startswith(("signal ", "func ", "const ", "var "))
                and "=" not in stripped
            ):
                continue
            if ".connect(" in line or "is_connected" in line or ".disconnect(" in line:
                continue
            found.append((source, index, function))
    return found


def _reachable(project: Project) -> set[str]:
    """Functions something that runs can get to: engine callbacks, what scenes connect,
    and, from those, every function their bodies name (calls, connects, call("name")).
    Names are matched across scripts, so a function counts as reached when any
    function of that name is."""
    bodies: dict[str, list[str]] = {}
    roots: set[str] = set(_CALLBACKS)
    for source in project.scenes.values():
        roots.update(_IDENT.findall(source.text))
    for source in project.scripts.values():
        function = ""
        indent = 0
        for raw in source.lines:
            named = _FUNC.match(raw)
            if named is not None:
                function = named.group(1)
                indent = len(raw) - len(raw.lstrip())
                bodies.setdefault(function, [])
                continue
            text = code_part(raw)
            if not text.strip():
                continue
            if function and len(raw) - len(raw.lstrip()) <= indent:
                function = ""
            if function:
                bodies[function].append(text)
            else:
                roots.update(_IDENT.findall(text))
    reached: set[str] = set()
    queue = [name for name in roots if name in bodies]
    while queue:
        name = queue.pop()
        if name in reached:
            continue
        reached.add(name)
        for text in bodies.get(name, []):
            queue += [
                w for w in _IDENT.findall(text) if w in bodies and w not in reached
            ]
    return reached


def endings(project: Project) -> list[Finding]:
    """The brief promises a way to win or lose: some code must reach it."""
    section = brief_section(project, "Win, lose")
    found: list[Finding] = []
    if section is None:
        return found
    for key, (label, verb, pattern, signal) in _GOAL_WORDS.items():
        promise = _promise(section.text(), key)
        if promise is None:
            continue
        said_line, said = promise
        line = next((n for n, t in section.body if said_line in t), section.line)
        evidence = _brief_ev(section, line, said_line)
        emitters = _emitters(project, pattern)
        if not emitters:
            found.append(
                make(
                    f"no-{key}",
                    "high",
                    f"No way to {verb}: nothing ends the level",
                    f"The player can never {verb}"
                    + (
                        " the game."
                        if key == "win"
                        else "; nothing they do has a cost."
                    ),
                    f'The brief says "{said_line}", but no script emits {signal} or '
                    f"shows a {'win' if key == 'win' else 'game over'} screen.",
                    evidence,
                    f"Make the game end the level when the player {said.lower()}: emit "
                    f"GameEvents.{signal} there and show the result, as the brief's "
                    "Win, lose, progression section says.",
                )
            )
            continue
        reachable = _reachable(project)
        reached = [e for e in emitters if not e[2] or e[2] in reachable]
        if not reached:
            source, at, function = emitters[0]
            found.append(
                make(
                    f"unreachable-{key}",
                    "high",
                    f"The {label.lower()} state is never reached",
                    f"The player can never {verb}: the code that would end the level never runs.",
                    f"`{function}` emits it ({source.path}:{at}), but nothing the game "
                    f'runs calls or connects `{function}`. The brief says "{said_line}".',
                    [Evidence(path=source.path, line=at, node=function)] + evidence,
                    f"Connect `{function}` to what the brief says {verb}s the level "
                    f"({said.lower()}).",
                )
            )
    return found


# --- 4. controls shown to the player ------------------------------------------------

_SHOWS = re.compile(
    r"(?i)\b(?:controls|how to play|tutorial|key ?bindings?|keybinds?)\b"
    r"|\b(?:press|hold|tap|click)\s+(?:\[|the\s+\w|[A-Z0-9]\b|space|enter|shift|esc)"
)
_EVENT_TEXT = re.compile(r"action_get_events|get_action_events|InputMap\.action")


def _texts(source: Text) -> list[str]:
    if source.path.endswith((".tscn", ".tres")):
        return re.findall(
            r'(?m)^(?:text|tooltip_text|placeholder_text)\s*=\s*"((?:[^"\\]|\\.)*)"',
            source.text,
        )
    out: list[str] = []
    for line in source.lines:
        out += strings(line)
    return out


def controls(project: Project) -> list[Finding]:
    """The game has gameplay actions, and nothing on screen ever shows them."""
    actions = [
        a
        for a in _gameplay_actions(project)
        if _bound(project.actions[a]) and not a.startswith(_SESSION_ACTIONS)
    ]
    if not actions or project.godot is None:
        return []
    for source in project.code().values():
        if source.path.endswith(SCRIPTS) and _EVENT_TEXT.search(source.text):
            return []
        if any(_SHOWS.search(text) for text in _texts(source)):
            return []
    evidence = [
        Evidence(path="project.godot", line=_input_line(project), node="[input]")
    ]
    section = brief_section(project, "Controls")
    if section is not None:
        evidence.append(
            Evidence(
                path=brief_mod.BRIEF_PATH.as_posix(),
                line=section.line,
                node=section.heading,
            )
        )
    names = ", ".join(brief_mod.action_label(a) for a in actions[:4])
    return [
        make(
            "controls-hidden",
            "medium",
            "The controls are never shown",
            "A new player has to guess which keys do what.",
            f"The game has {len(actions)} gameplay actions ({names}…), but no menu, "
            "label or hint shows them.",
            evidence,
            "Show the controls to the player: a short hint on the first screen of play, "
            "or a Controls page in the menu, read from the input map so it stays right "
            "when bindings change.",
        )
    ]


SCRIPTS = (".gd", ".cs")


# --- 5. pickups that give no feedback -----------------------------------------------

_PICKUP_WORDS = re.compile(
    r"(?i)coin|pickup|pick_up|collect|item|gem|key|heart|health|power|ammo|token|star|orb|loot"
)
_GONE = re.compile(r"queue_free\(\)|\bhide\(\)|visible\s*=\s*false")
_FEEDBACK = re.compile(
    r"AudioStreamPlayer|\.play\(|Particles|emitting|create_tween|Tween\b|AnimationPlayer"
    r"|\.emit\(|emit_signal|score|request_action|GameEvents|sound|sfx|flash|shake"
)


def pickups(project: Project) -> list[Finding]:
    """Something the player collects vanishes with no sound, effect or count."""
    found: list[Finding] = []
    for path, source in project.scripts.items():
        if not re.search(r"(?m)^extends\s+Area[23]D", source.text):
            continue
        if not re.search(r"body_entered|area_entered", source.text):
            continue
        if not _PICKUP_WORDS.search(path.rsplit("/", 1)[-1]):
            continue
        code = "\n".join(code_part(line) for line in source.lines)
        gone = _GONE.search(code)
        if gone is None or _FEEDBACK.search(code):
            continue
        scenes = [s for s in project.scenes.values() if path in s.text]
        if any(_FEEDBACK.search(s.text) for s in scenes):
            continue
        line = source.find(_GONE)
        name = Path(path).stem.replace("_", " ")
        found.append(
            make(
                "silent-pickup",
                "medium",
                f"Picking up a {name} gives no feedback",
                "It just disappears: no sound, no effect, no count goes up, so the "
                "player can't tell it mattered.",
                f"{path} removes or hides it on contact and nothing else happens: no "
                "sound, particles, animation, tween or signal.",
                [
                    Evidence(
                        path=path, line=line, quote=source.lines[line - 1].strip()[:120]
                    )
                ],
                f"Give the {name} pickup feedback when collected: a short sound and a "
                "small visual effect, and update any count the HUD shows.",
            )
        )
    return found


# --- 6. the same movement tuned differently from scene to scene -------------------

_TUNING = re.compile(
    r"(?i)^(?:jump|run|walk|move|max_?speed|speed|gravity|fall|air|accel|decel|dash|sprint|climb)\w*$"
)
_EXT = re.compile(r'^\[ext_resource [^\]]*path="res://([^"]+)"[^\]]*id="([^"]+)"')
_NODE = re.compile(r'^\[node name="([^"]+)"([^\]]*)\]')
_INSTANCE = re.compile(r'instance=ExtResource\(\s*"([^"]+)"\s*\)')
_SCRIPT = re.compile(r'^script\s*=\s*ExtResource\(\s*"([^"]+)"\s*\)')
_PROP = re.compile(r"^([A-Za-z_]\w*)\s*=\s*(-?\d+(?:\.\d+)?(?:e-?\d+)?)\s*$")
_EXPORT = re.compile(
    r"^\s*(?:@export\w*(?:\([^)]*\))?\s+)+var\s+([A-Za-z_]\w*)\s*(?::\s*\w+)?\s*:?=\s*(-?\d+(?:\.\d+)?)"
)
_CONST = re.compile(
    r"^\s*const\s+([A-Z_][A-Z0-9_]*)\s*(?::\s*\w+)?\s*:?=\s*(-?\d+(?:\.\d+)?)"
)


def _number(text: str) -> float:
    return float(text)


def _scene_nodes(
    source: Text,
) -> list[tuple[str, int, str, str, dict[str, tuple[float, int]]]]:
    """(name, line, instanced path, script path, numeric properties) per node."""
    ext: dict[str, str] = {}
    nodes: list[tuple[str, int, str, str, dict[str, tuple[float, int]]]] = []
    for index, line in enumerate(source.lines, start=1):
        match = _EXT.match(line)
        if match:
            ext[match.group(2)] = match.group(1)
            continue
        node = _NODE.match(line)
        if node:
            instance = _INSTANCE.search(node.group(2))
            nodes.append(
                (
                    node.group(1),
                    index,
                    ext.get(instance.group(1), "") if instance else "",
                    "",
                    {},
                )
            )
            continue
        if line.startswith("["):
            if nodes and not line.startswith("[node"):
                # A sub-resource: the node's properties stop here.
                nodes.append(("", index, "", "", {}))
            continue
        if not nodes:
            continue
        script = _SCRIPT.match(line)
        if script:
            name, at, inst, _, props = nodes[-1]
            nodes[-1] = (name, at, inst, ext.get(script.group(1), ""), props)
            continue
        prop = _PROP.match(line)
        if prop and _TUNING.match(prop.group(1)):
            nodes[-1][4][prop.group(1)] = (_number(prop.group(2)), index)
    return [n for n in nodes if n[0]]


def _defaults(project: Project, script_path: str) -> dict[str, tuple[float, int]]:
    source = project.scripts.get(script_path)
    if source is None:
        return {}
    values: dict[str, tuple[float, int]] = {}
    for index, line in enumerate(source.lines, start=1):
        match = _EXPORT.match(line)
        if match and _TUNING.match(match.group(1)):
            values[match.group(1)] = (_number(match.group(2)), index)
    return values


def _label(name: str) -> str:
    return name.replace("_", " ").strip().capitalize()


def tuning(project: Project) -> list[Finding]:
    """One player scene instanced in several scenes with different movement values, or
    several player scripts that tune the same movement differently."""
    found: list[Finding] = []
    # Instances: (instanced scene, property) -> [(scene, line, value)]
    seen: dict[tuple[str, str], list[tuple[str, int, float]]] = {}
    roots: dict[str, dict[str, tuple[float, int]]] = {}
    for path, source in project.scenes.items():
        nodes = _scene_nodes(source)
        if nodes and not nodes[0][2]:
            root = nodes[0]
            values = dict(_defaults(project, root[3]))
            values.update(root[4])
            roots[path] = values
    for path, source in project.scenes.items():
        for _name, line, inst, _script, props in _scene_nodes(source):
            if not inst or inst not in roots:
                continue
            base = roots[inst]
            for prop in set(base) | set(props):
                if not _TUNING.match(prop):
                    continue
                value, at = props.get(prop, (base.get(prop, (None, 0))[0], line))
                if value is None:
                    continue
                seen.setdefault((inst, prop), []).append((path, at, value))
    for (inst, prop), uses in sorted(seen.items()):
        distinct = {round(v, 4) for _, _, v in uses}
        if len({p for p, _, _ in uses}) < 2 or len(distinct) < 2:
            continue
        uses.sort(key=lambda u: u[0])
        shown = ", ".join(f"{Path(p).name} {v:g}" for p, _, v in uses[:4])
        found.append(
            make(
                "uneven-tuning",
                "medium",
                f"{_label(prop)} differs between scenes ({shown})",
                "The same character moves differently from one level to the next, so "
                "jumps the player learned stop working.",
                f"{Path(inst).name} is used in {len(uses)} scenes with different "
                f"{prop} values.",
                [Evidence(path=p, line=at, node=prop) for p, at, _ in uses[:4]],
                f"Use one {prop} for {Path(inst).name} everywhere (set it in the "
                "player scene and drop the per-scene overrides), unless the brief says a "
                "level should feel different.",
                key=f"{inst}:{prop}",
            )
        )
    # Player scripts: the same tuning name with different values.
    players: dict[str, list[tuple[str, int, float]]] = {}
    for path, source in project.scripts.items():
        if "player" not in Path(path).stem.lower():
            continue
        if not re.search(r"(?m)^extends\s+CharacterBody[23]D", source.text):
            continue
        for index, text in enumerate(source.lines, start=1):
            match = _CONST.match(text) or _EXPORT.match(text)
            if match and _TUNING.match(match.group(1)):
                players.setdefault(match.group(1).lower(), []).append(
                    (path, index, _number(match.group(2)))
                )
    for prop, uses in sorted(players.items()):
        if (
            len({p for p, _, _ in uses}) < 2
            or len({round(v, 4) for _, _, v in uses}) < 2
        ):
            continue
        shown = ", ".join(f"{Path(p).name} {v:g}" for p, _, v in uses[:4])
        found.append(
            make(
                "uneven-tuning",
                "medium",
                f"{_label(prop)} differs between player scripts ({shown})",
                "The player's character moves differently depending on which scene "
                "they are in.",
                f"{len(uses)} player scripts set {prop} to different values.",
                [Evidence(path=p, line=at, node=prop) for p, at, _ in uses[:4]],
                f"Use one player script, or one {prop} value, for every scene the "
                "player plays, unless the brief says a character should feel different.",
                key=f"players:{prop}",
            )
        )
    return found


# --- 7. blockouts: objectives that can't be reached, rooms that trap ---------------

#: Seconds the blockout walk may take in all; rooms past it are not walked.
WALK_BUDGET = 8.0
MAX_ROOMS = 16


def _layout_line(source: Text, ident: str, table: str) -> int:
    for index, line in enumerate(source.lines):
        if re.match(rf'^\s*id\s*=\s*"{re.escape(ident)}"', line):
            for back in range(index, -1, -1):
                if source.lines[back].strip().startswith(f"[[{table}"):
                    return back + 1
            return index + 1
    return source.find(rf"^\[\[{table}\]\]")


def _centre(room: object) -> tuple[float, float] | None:
    rect = getattr(room, "rect", None)
    if rect:
        return rect[0] + rect[2] / 2, rect[1] + rect[3] / 2
    polygon = getattr(room, "polygon", None)
    if polygon:
        return (
            sum(p[0] for p in polygon) / len(polygon),
            sum(p[1] for p in polygon) / len(polygon),
        )
    return None


def levels(project: Project) -> list[Finding]:
    """Walk each blockout layout: goals the spawn can't reach, and rooms the player can
    get into but never get out of toward the goal."""
    from joinery import blockout
    from joinery.blockout.check import check as check_layout
    from joinery.blockout.layout import LayoutError, Marker, Spawn
    from joinery.blockout.world import profile_for

    folder = project.root / blockout.DESIGN_DIR
    if not folder.is_dir():
        return []
    found: list[Finding] = []
    blueprint = blockout.project_blueprint(project.root)
    started = time.monotonic()
    for path in sorted(folder.glob("*.toml")):
        rel = path.relative_to(project.root).as_posix()
        source = Text(rel, path.read_text(encoding="utf-8"))
        try:
            layout = blockout.parse(source.text)
        except LayoutError as exc:
            project.skipped.append(f"{rel}: {exc}")
            continue
        profile = profile_for(blueprint, layout.mode)
        walked = check_layout(layout, profile)
        if walked.reach is None:
            project.skipped.append(
                f"{rel}: the layout has problems, so it wasn't walked"
            )
            continue
        for target in walked.reach.targets:
            if target.reached:
                continue
            line = _layout_line(source, target.id, "markers")
            found.append(
                make(
                    "unreachable",
                    "high",
                    f"The {target.kind} {target.id} can't be reached",
                    f"The player can never get to the {target.kind} from where they start.",
                    target.reason or f"No walk from the spawn reaches {target.id}.",
                    [Evidence(path=rel, line=line, node=target.id)],
                    f"Change {rel} so the spawn reaches {target.id} (an opening, "
                    "stairs or a lower ledge), then rebuild the blockout.",
                    key=f"{rel}:{target.id}",
                )
            )
        goals = [m for m in layout.markers if m.kind == "goal"]
        if not goals or layout.spawn is None:
            continue
        rooms = [(room, _centre(room)) for room in layout.rooms[:MAX_ROOMS]]
        probes = [
            Marker(kind="checkpoint", at=[c[0], c[1]], id=f"jdprobe{i}")
            for i, (_, c) in enumerate(rooms)
            if c is not None
        ]
        if not probes:
            continue
        probed = check_layout(
            layout.model_copy(update={"markers": list(layout.markers) + probes}),
            profile,
        )
        if probed.reach is None:
            continue
        inside = {t.id for t in probed.reach.targets if t.reached}
        reached_goals = {
            t.id for t in walked.reach.targets if t.reached and t.kind == "goal"
        }
        for index, (room, centre) in enumerate(rooms):
            if time.monotonic() - started > WALK_BUDGET:
                project.skipped.append(f"{rel}: not every room was walked (time)")
                break
            if centre is None or f"jdprobe{index}" not in inside:
                continue
            moved = layout.model_copy(
                update={
                    "spawn": Spawn(
                        at=[centre[0], centre[1]], facing=layout.spawn.facing
                    ),
                    "markers": goals,
                }
            )
            out = check_layout(moved, profile)
            if out.reach is None or not out.reach.spawn_ok:
                continue
            stuck = [
                t.id
                for t in out.reach.targets
                if not t.reached and t.id in reached_goals
            ]
            if not stuck:
                continue
            found.append(
                make(
                    "softlock",
                    "high",
                    f"Room {room.id} traps the player",
                    f"A player who goes into {room.id} can't get back out to the goal and "
                    "has to restart.",
                    f"The spawn reaches {room.id}, but from inside it no walk, jump or "
                    f"drop reaches {', '.join(stuck)}.",
                    [
                        Evidence(
                            path=rel,
                            line=_layout_line(source, room.id, "rooms"),
                            node=room.id,
                        )
                    ],
                    f"Give {room.id} in {rel} a way back out (stairs, a ledge low enough "
                    "to climb, or an opening) or keep the player from dropping into it, "
                    "then rebuild the blockout.",
                    key=f"{rel}:{room.id}",
                )
            )
    return found


# --- 8. what the newest playtest saw ----------------------------------------------

#: Failures within this many seconds of each other count as one stretch of play.
SPIKE_WINDOW = 20.0


def _newest_report(project: Project) -> tuple[Path, dict[str, object]] | None:
    folder = project.root / ".joinery" / "playtests"
    if not folder.is_dir():
        return None
    for run in sorted((p for p in folder.iterdir() if p.is_dir()), reverse=True):
        path = run / "report.json"
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(data, dict):
                return path, data
    return None


def _seconds(value: object) -> float:
    return float(value) if isinstance(value, (int, float)) else 0.0


def _stamp(seconds: float) -> str:
    whole = max(0, int(seconds))
    return f"{whole // 60}:{whole % 60:02d}"


def playtests(project: Project) -> list[Finding]:
    """Softlocks, falls out of the world, difficulty and unclear goals the newest
    playtest found (a bot played, so these are evidence, not verdicts)."""
    newest = _newest_report(project)
    if newest is None:
        return []
    path, report = newest
    rel = path.relative_to(project.root).as_posix()
    seed = str(report.get("seed", ""))
    found: list[Finding] = []
    items = report.get("items", [])
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind", ""))
        detail = str(item.get("detail", ""))
        t = item.get("t")
        when = f" at {_stamp(float(t))}" if isinstance(t, (int, float)) else ""
        evidence = [Evidence(path=rel, node=f"seed {seed}{when}", quote=detail[:160])]
        where = str(item.get("where", "") or "")
        if where.startswith("res://"):
            file, _, line = where.removeprefix("res://").partition(":")
            if (project.root / file).is_file():
                evidence.insert(
                    0, Evidence(path=file, line=int(line) if line.isdigit() else 0)
                )
        if kind == "softlock":
            found.append(
                make(
                    "softlock",
                    "high",
                    f"The playtester got stuck{when}",
                    "A player can reach a spot where nothing they press gets them out.",
                    detail or "The playtest bot found no way to move on.",
                    evidence,
                    f"Find where the player gets stuck (replay: joinery playtest --seed "
                    f"{seed}) and give them a way out.",
                    key=f"{rel}:{item.get('id', '')}",
                )
            )
        elif kind == "fell":
            found.append(
                make(
                    "out-of-world",
                    "high",
                    f"The player fell out of the world{when}",
                    "The player drops through the level and the game never brings them back.",
                    detail,
                    evidence,
                    f"Close the gap the player fell through, or send a falling player "
                    f"back to the last checkpoint (replay: joinery playtest --seed {seed}).",
                    key=f"{rel}:{item.get('id', '')}",
                )
            )
        elif kind == "balance" and "too hard" in detail:
            found.append(
                make(
                    "difficulty",
                    "medium",
                    "The playtester kept dying",
                    "A first-time player may give up before they finish.",
                    detail,
                    evidence,
                    "Look at what kills the player most in this run and make the first "
                    "encounter with it easier to read (a warning, more room or a "
                    f"checkpoint before it). Replay: joinery playtest --seed {seed}.",
                    key=f"{rel}:hard",
                )
            )
        elif kind == "balance" and "hard to find" in detail:
            found.append(
                make(
                    "goal-unclear",
                    "medium",
                    "The goal is hard to find",
                    "A player doesn't know what to do or where to go.",
                    detail,
                    evidence,
                    "Make the level's goal visible from the start: a marker, a line of "
                    "text or a path that leads to it.",
                    key=f"{rel}:goal",
                )
            )
    goals = report.get("goals", {})
    failed = goals.get("failed", []) if isinstance(goals, dict) else []
    times = sorted(
        float(f["t"])
        for f in failed
        if isinstance(f, dict) and isinstance(f.get("t"), (int, float))
    )
    if len(times) >= 3:
        best = (0, 0.0)
        for start in range(len(times)):
            count = sum(1 for t in times[start:] if t - times[start] <= SPIKE_WINDOW)
            if count > best[0]:
                best = (count, times[start])
        count, at = best
        if (
            count >= 3
            and count >= 0.6 * len(times)
            and _seconds(report.get("played_seconds")) >= 60
        ):
            reasons = [
                str(f.get("reason", ""))
                for f in failed
                if isinstance(f, dict)
                and at <= _seconds(f.get("t", -1)) <= at + SPIKE_WINDOW
            ]
            common = max(set(reasons), key=reasons.count) if reasons else ""
            found.append(
                make(
                    "difficulty-spike",
                    "medium",
                    f"Difficulty spike around {_stamp(at)}",
                    f"{count} of {len(times)} failures happen within {int(SPIKE_WINDOW)} "
                    "seconds of each other; the rest of the run is much easier.",
                    "A sudden jump in difficulty is where players quit."
                    + (f" Most were: {common}." if common else ""),
                    [
                        Evidence(
                            path=rel,
                            node=f"seed {seed} at {_stamp(at)}",
                            quote=common[:120],
                        )
                    ],
                    f"Look at what the player meets around {_stamp(at)} in this run and "
                    f"ease it into the curve. Replay: joinery playtest --seed {seed}.",
                    key=f"{rel}:spike",
                )
            )
    return found


CHECKS: tuple[Check, ...] = (
    verbs,
    mechanics,
    endings,
    controls,
    pickups,
    tuning,
    levels,
    playtests,
)
