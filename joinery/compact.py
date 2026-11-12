"""Short tool results for agents: a summary and a reference, the rest on request.

A tool result stays in an agent's conversation for every later request of the
session, so a 40k-character scene tree or check log read once is paid for on
every turn after it. The gateway answers each agent call with the result as it
is when it is small, and otherwise with a summary: long lists keep their first
items, long texts their first and last lines, and each cut says how much is
left out. The full result is kept under ``.joinery/results/`` and the summary
carries its reference; ``details(ref, path, offset)`` pages through it.

The same for every agent (Claude Code, Codex, Gemini CLI, opencode): it is the
gateway's answer, not a provider feature. Nothing here reads or changes what a
tool did, only how much of its answer reaches the agent at once.
"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any

#: Where the full results are kept, under the project.
RESULTS = ".joinery/results"
#: Results kept; the oldest go first.
KEEP = 200
#: A result whose JSON is at most this many characters reaches the agent as it is
#: (about 1.5k tokens at four characters a token).
BUDGET = 6000
#: How much each pass keeps: items of a list, then head and tail lines of a text.
PASSES: tuple[tuple[int, int, int], ...] = (
    (20, 30, 10),
    (10, 15, 5),
    (5, 8, 3),
    (3, 4, 2),
)
#: Characters kept of one long line.
LINE_KEEP = 400
#: One page of ``details``: lines, and characters at most.
PAGE_LINES = 150
PAGE_CHARS = 12000
#: Content parts kept whole: their bytes are not text for the agent to read.
_MEDIA = frozenset({"image", "audio", "resource", "resource_link"})
_REF = re.compile(r"^r[0-9a-f]{12}$")


def _measured(value: Any) -> Any:
    """``value`` with media data left out, for measuring what text it holds."""
    if isinstance(value, dict):
        if value.get("type") in _MEDIA:
            return {k: v for k, v in value.items() if k not in {"data", "blob"}}
        return {k: _measured(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_measured(v) for v in value]
    return value


def size(value: Any) -> int:
    """Characters of ``value`` as an agent reads it (JSON), media data aside."""
    return len(json.dumps(_measured(value), ensure_ascii=False))


class _Cuts:
    """What a summary left out, by where."""

    def __init__(self) -> None:
        self.items: list[str] = []

    def add(self, where: str, count: int, unit: str) -> None:
        self.items.append(f"{where or 'result'}: {count} more {unit}")


def _text(value: str, where: str, head: int, tail: int, cuts: _Cuts) -> str:
    lines = value.split("\n")
    if len(lines) > head + tail + 1:
        hidden = len(lines) - head - tail
        cuts.add(where, hidden, "lines")
        lines = [
            *lines[:head],
            f"… {hidden} more lines: ask for details",
            *lines[len(lines) - tail :],
        ]
    shortened = []
    for line in lines:
        if len(line) > LINE_KEEP:
            cuts.add(where, len(line) - LINE_KEEP, "characters")
            line = line[:LINE_KEEP] + f"… ({len(line) - LINE_KEEP} more characters)"
        shortened.append(line)
    return "\n".join(shortened)


def _shrink(
    value: Any, where: str, keep: int, head: int, tail: int, cuts: _Cuts
) -> Any:
    if isinstance(value, dict):
        if value.get("type") in _MEDIA:
            return value
        return {
            k: _shrink(v, f"{where}/{k}" if where else str(k), keep, head, tail, cuts)
            for k, v in value.items()
        }
    if isinstance(value, list):
        kept = [
            _shrink(v, f"{where}/{i}", keep, head, tail, cuts)
            for i, v in enumerate(value[:keep])
        ]
        if len(value) > keep:
            hidden = len(value) - keep
            cuts.add(where, hidden, "items")
            kept.append(f"… {hidden} more items: ask for details")
        return kept
    if isinstance(value, str) and len(value) > LINE_KEEP:
        stripped = value.lstrip()
        if stripped[:1] in {"[", "{"}:
            try:
                decoded = json.loads(value)
            except ValueError:
                decoded = None
            if isinstance(decoded, list | dict):
                # A JSON document carried as a string (the editor's node list).
                return json.dumps(
                    _shrink(decoded, where, keep, head, tail, cuts),
                    ensure_ascii=False,
                )
        return _text(value, where, head, tail, cuts)
    return value


def summarise(value: Any, budget: int = BUDGET) -> tuple[Any, list[str]]:
    """``value`` cut down to about ``budget`` characters, and what was left out.

    Each pass keeps less; the last one is returned even when it is still over.
    Scalars (status, counts, paths) are never cut, so a summary answers the same
    yes/no questions the full result does.
    """
    if size(value) <= budget:
        return value, []
    short: Any = value
    cuts = _Cuts()
    for keep, head, tail in PASSES:
        cuts = _Cuts()
        short = _shrink(value, "", keep, head, tail, cuts)
        if size(short) <= budget:
            break
    return short, cuts.items


def _directory(project: Path) -> Path:
    return project / RESULTS


def store(project: Path, tool: str, value: Any) -> str:
    """Keep a full result; returns its reference."""
    ref = "r" + uuid.uuid4().hex[:12]
    folder = _directory(project)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{ref}.json").write_text(
        json.dumps({"tool": tool, "result": value}, ensure_ascii=False),
        encoding="utf-8",
    )
    kept = sorted(folder.glob("r*.json"), key=lambda p: p.stat().st_mtime_ns)
    for old in kept[:-KEEP]:
        old.unlink(missing_ok=True)
    return ref


def compact(project: Path, tool: str, value: Any, budget: int = BUDGET) -> Any:
    """The result an agent gets: ``value`` itself when small, else a summary with a
    ``details`` entry naming its reference and what was left out."""
    short, cuts = summarise(value, budget)
    if not cuts:
        return value
    ref = store(project, tool, value)
    note = {
        "ref": ref,
        "full_characters": size(value),
        "left_out": cuts[:8]
        + ([f"and {len(cuts) - 8} more cuts"] if len(cuts) > 8 else []),
        "how": f"This is a summary. details(ref='{ref}') pages the full result; "
        f"details(ref='{ref}', path='a/b/3') reads one part of it.",
    }
    if isinstance(short, dict):
        return {**short, "details": note}
    return {"result": short, "details": note}


#: Nodes listed in the editor outline: in the prompt's project map, and by
#: inspect_editor.
OUTLINE_NODES = 80
INSPECT_NODES = 120


def _rows(context: dict[str, str]) -> list[dict[str, Any]]:
    try:
        rows = json.loads(context.get("nodes", "") or "[]")
    except ValueError:
        return []
    return (
        [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
    )


def _number(value: Any) -> str:
    return f"{float(value):g}" if isinstance(value, int | float) else str(value)


def _outline_line(row: dict[str, Any]) -> str:
    ident = str(row.get("id", ""))
    path = ident.partition("::")[2] or str(row.get("name", ""))
    line = f"{path} ({row.get('type', '')})"
    position = row.get("position")
    if isinstance(position, list) and any(position):
        line += " at " + ",".join(_number(round(float(v), 2)) for v in position)
    if row.get("script"):
        line += f" script={row['script']}"
    if row.get("source"):
        line += f" from={row['source']}"
    return line


def editor_outline(
    context: dict[str, str], limit: int = OUTLINE_NODES
) -> dict[str, Any]:
    """The editor context with its node list as an outline: one line per node (path,
    type, position, script, the scene it comes from), no properties, at most ``limit``
    nodes in the editor's breadth-first order. A node's id is the scene path, ``::``
    and the node path; ``inspect_editor(node=…)`` gives its properties."""
    rows = _rows(context)
    out: dict[str, Any] = {k: v for k, v in context.items() if k != "nodes"}
    if not rows:
        if "nodes" in context:
            out["nodes"] = context["nodes"]
        return out
    out["nodes"] = [_outline_line(row) for row in rows[:limit]]
    scene = str(rows[0].get("id", "")).partition("::")[0]
    note = f"Node ids are {scene}::<path>. inspect_editor(node=<path or name>) gives a node's properties."
    if len(rows) > limit:
        note = (
            f"{len(rows) - limit} more nodes not listed (the scene file has them all). "
            + note
        )
    out["nodes_note"] = note
    return out


def editor_nodes(context: dict[str, str], node: str) -> dict[str, Any]:
    """The full rows (properties included) of the nodes named ``node``: an id, a path
    in the scene, or a name."""
    wanted = node.strip()
    found = [
        row
        for row in _rows(context)
        if wanted
        and (
            row.get("id") == wanted
            or str(row.get("id", "")).partition("::")[2] == wanted
            or row.get("name") == wanted
        )
    ]
    if not found:
        raise ValueError(
            f"No node {wanted!r} in the editor's scene; inspect_editor lists them."
        )
    return {"scene_path": context.get("scene_path", ""), "nodes": found[:10]}


def _part(value: Any, path: str) -> Any:
    for step in [s for s in re.split(r"[/.]", path) if s]:
        if isinstance(value, str) and value.lstrip()[:1] in {"[", "{"}:
            try:
                value = json.loads(value)
            except ValueError:
                pass
        if isinstance(value, dict) and step in value:
            value = value[step]
        elif isinstance(value, list) and step.isdigit() and int(step) < len(value):
            value = value[int(step)]
        else:
            raise ValueError(f"No part {step!r} at {path!r} in this result.")
    return value


def details(
    project: Path, ref: str, path: str = "", offset: int = 0, limit: int = PAGE_LINES
) -> dict[str, Any]:
    """One page of a kept result (or of one part of it), as text lines."""
    if not _REF.match(ref):
        raise ValueError("Unknown result reference.")
    file = _directory(project) / f"{ref}.json"
    if not file.is_file():
        raise ValueError(
            "That result is no longer kept; run the tool again for a fresh one."
        )
    kept = json.loads(file.read_text(encoding="utf-8"))
    value = _part(kept["result"], path)
    if isinstance(value, str) and value.lstrip()[:1] in {"[", "{"}:
        try:
            value = json.loads(value)
        except ValueError:
            pass
    text = (
        value
        if isinstance(value, str)
        else json.dumps(value, indent=1, ensure_ascii=False)
    )
    lines = text.split("\n")
    start = max(offset, 0)
    page: list[str] = []
    used = 0
    for line in lines[start : start + max(1, min(limit, 1000))]:
        if page and used + len(line) > PAGE_CHARS:
            break
        page.append(line[:PAGE_CHARS])
        used += len(line) + 1
    end = start + len(page)
    return {
        "ref": ref,
        "tool": kept.get("tool", ""),
        "path": path,
        "lines": "\n".join(page),
        "from_line": start,
        "total_lines": len(lines),
        "next_offset": end if end < len(lines) else None,
    }
