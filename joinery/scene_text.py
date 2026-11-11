"""Godot scene text (`.tscn`) read and patched without Godot.

Enough of the format for the sound pass: the sections with their attributes and
property lines, each node's path, and three edits that stock Godot loads as it
would its own output: add an external resource (reused when the path is
already there), add a sub-resource, add a node (before the connections, so it
always follows its parent), and set properties on a node. Sections that are not
touched keep their text byte for byte, so a review shows only the change.

`set_setting` does the same for one key of `project.godot`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_HEADER = re.compile(
    r"^\[(gd_scene|gd_resource|ext_resource|sub_resource|node|connection|editable|resource)\b(.*)\]\s*$"
)
_ATTR = re.compile(r'([\w/]+)=("(?:[^"\\]|\\.)*"|\[[^\]]*\]|\w+\([^)]*\)|[^\s\]]+)')
_PROP = re.compile(r"^([A-Za-z_][\w/:.]*) = (.*)$")


def quote(text: str) -> str:
    """A Godot string literal."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def string_name(text: str) -> str:
    return "&" + quote(text)


def unquote(value: str) -> str:
    value = value.strip()
    value = value.removeprefix("&")
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return value


def number(value: float) -> str:
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return "0" if text in {"-0", ""} else text


@dataclass
class Section:
    kind: str
    attrs: dict[str, str]
    #: The header line and every line after it, up to the next section.
    lines: list[str]
    changed: bool = False

    def attr(self, key: str) -> str:
        return unquote(self.attrs.get(key, ""))

    def props(self) -> dict[str, str]:
        """Property name -> raw value text (a value may span several lines)."""
        found: dict[str, str] = {}
        current = ""
        for line in self.lines[1:]:
            match = _PROP.match(line)
            if match:
                current = match.group(1)
                found[current] = match.group(2)
            elif current and line.strip():
                found[current] += "\n" + line
        return found

    def set(self, key: str, value: str) -> None:
        """Set one property line, replacing a value of any length."""
        body = self.lines[1:]
        start = next(
            (i for i, line in enumerate(body) if line.startswith(key + " = ")), None
        )
        if start is None:
            end = len(body)
            while end > 0 and not body[end - 1].strip():
                end -= 1
            body[end:end] = [f"{key} = {value}"]
        else:
            stop = start + 1
            while (
                stop < len(body) and body[stop].strip() and not _PROP.match(body[stop])
            ):
                stop += 1
            body[start:stop] = [f"{key} = {value}"]
        self.lines = [self.lines[0], *body]
        self.changed = True


@dataclass
class Node:
    name: str
    type: str
    parent: str
    path: str
    section: Section
    instance: str = ""
    groups: list[str] = field(default_factory=list)

    def props(self) -> dict[str, str]:
        return self.section.props()


class Scene:
    """A `.tscn` (or `.tres`) file as sections."""

    def __init__(self, text: str) -> None:
        self.preamble: list[str] = []
        self.sections: list[Section] = []
        for line in text.splitlines():
            match = _HEADER.match(line)
            if match:
                attrs = {k: v for k, v in _ATTR.findall(match.group(2))}
                self.sections.append(Section(match.group(1), attrs, [line]))
            elif self.sections:
                self.sections[-1].lines.append(line)
            else:
                self.preamble.append(line)
        self._ids = {s.attr("id") for s in self.sections if s.attrs.get("id")}

    def text(self) -> str:
        lines = list(self.preamble)
        for index, section in enumerate(self.sections):
            body = list(section.lines)
            following = (
                self.sections[index + 1] if index + 1 < len(self.sections) else None
            )
            if section.changed and following is not None:
                while len(body) > 1 and not body[-1].strip():
                    body.pop()
                # Godot writes external resources as one block, other sections apart.
                if not (section.kind == following.kind == "ext_resource"):
                    body.append("")
            lines += body
        return "\n".join(lines).rstrip("\n") + "\n"

    # --- Reading ----------------------------------------------------------------

    def of_kind(self, kind: str) -> list[Section]:
        return [s for s in self.sections if s.kind == kind]

    def ext(self, identifier: str) -> Section | None:
        for section in self.of_kind("ext_resource"):
            if section.attr("id") == identifier:
                return section
        return None

    def sub(self, identifier: str) -> Section | None:
        for section in self.of_kind("sub_resource"):
            if section.attr("id") == identifier:
                return section
        return None

    def ext_path(self, value: str) -> str:
        """The path an `ExtResource("id")` value points to ("" if it isn't one)."""
        match = re.match(r'^ExtResource\(\s*"([^"]+)"\s*\)$', value.strip())
        if not match:
            return ""
        found = self.ext(match.group(1))
        return found.attr("path") if found else ""

    def sub_of(self, value: str) -> Section | None:
        match = re.match(r'^SubResource\(\s*"([^"]+)"\s*\)$', value.strip())
        return self.sub(match.group(1)) if match else None

    def nodes(self) -> list[Node]:
        found: list[Node] = []
        for section in self.of_kind("node"):
            name = section.attr("name")
            parent = section.attr("parent") if "parent" in section.attrs else None
            if parent is None:
                path = "."
            elif parent == ".":
                path = name
            else:
                path = f"{parent}/{name}"
            groups = re.findall(r'"([^"]*)"', section.attrs.get("groups", ""))
            instance = ""
            if "instance" in section.attrs:
                instance = self.ext_path(section.attrs["instance"])
            found.append(
                Node(
                    name,
                    section.attr("type"),
                    parent or "",
                    path,
                    section,
                    instance,
                    groups,
                )
            )
        return found

    def node(self, path: str) -> Node | None:
        return next((n for n in self.nodes() if n.path == path), None)

    def script_of(self, node: Node) -> str:
        return self.ext_path(node.props().get("script", ""))

    def children(self, path: str) -> list[Node]:
        return [n for n in self.nodes() if n.parent == path and n.path != "."]

    # --- Patching ---------------------------------------------------------------

    def _new_id(self, stem: str) -> str:
        base = re.sub(r"[^A-Za-z0-9_]", "_", stem)[:40] or "res"
        identifier, count = base, 2
        while identifier in self._ids:
            identifier, count = f"{base}_{count}", count + 1
        self._ids.add(identifier)
        return identifier

    def _insert_at(self, index: int, section: Section) -> None:
        section.changed = True
        if index > 0:
            # The section before gets its blank separator line back if it lacked one.
            self.sections[index - 1].changed = True
        self.sections.insert(index, section)

    def _after_last(self, *kinds: str) -> int:
        last = 0
        for index, section in enumerate(self.sections):
            if section.kind in kinds:
                last = index + 1
        return last

    def add_ext_resource(self, kind: str, path: str, stem: str) -> str:
        """Reference a project file; returns its id (an existing one when it is there)."""
        for section in self.of_kind("ext_resource"):
            if section.attr("path") == path:
                return section.attr("id")
        identifier = self._new_id(stem)
        header = f"[ext_resource type={quote(kind)} path={quote(path)} id={quote(identifier)}]"
        index = self._after_last("gd_scene", "gd_resource", "ext_resource")
        self._insert_at(
            index,
            Section(
                "ext_resource", {"type": kind, "path": path, "id": identifier}, [header]
            ),
        )
        self._bump_steps()
        return identifier

    def add_sub_resource(self, kind: str, stem: str, props: dict[str, str]) -> str:
        identifier = self._new_id(stem)
        header = f"[sub_resource type={quote(kind)} id={quote(identifier)}]"
        index = self._after_last(
            "gd_scene", "gd_resource", "ext_resource", "sub_resource"
        )
        lines = [header] + [f"{key} = {value}" for key, value in props.items()]
        self._insert_at(
            index, Section("sub_resource", {"type": kind, "id": identifier}, lines)
        )
        self._bump_steps()
        return identifier

    def add_node(
        self,
        name: str,
        kind: str,
        parent: str,
        props: dict[str, str],
    ) -> str:
        """Add a node under ``parent`` (a node path, "." for the root); returns its path."""
        existing = {n.path for n in self.nodes()}
        unique, count = name, 2
        path = unique if parent == "." else f"{parent}/{unique}"
        while path in existing:
            unique, count = f"{name}{count}", count + 1
            path = unique if parent == "." else f"{parent}/{unique}"
        header = (
            f"[node name={quote(unique)} type={quote(kind)} parent={quote(parent)}]"
        )
        lines = [header] + [f"{key} = {value}" for key, value in props.items()]
        index = next(
            (
                i
                for i, s in enumerate(self.sections)
                if s.kind in {"connection", "editable"}
            ),
            len(self.sections),
        )
        self._insert_at(
            index,
            Section("node", {"name": unique, "type": kind, "parent": parent}, lines),
        )
        return path

    def set_props(self, path: str, props: dict[str, str]) -> None:
        found = self.node(path)
        if found is None:
            raise ValueError(f"No node {path} in the scene.")
        for key, value in props.items():
            found.section.set(key, value)

    def _bump_steps(self) -> None:
        head = self.sections[0] if self.sections else None
        if head is None or "load_steps" not in head.attrs:
            return
        try:
            steps = int(head.attrs["load_steps"]) + 1
        except ValueError:
            return
        head.attrs["load_steps"] = str(steps)
        head.lines[0] = re.sub(r"load_steps=\d+", f"load_steps={steps}", head.lines[0])


def dictionary(entries: dict[str, str]) -> str:
    """A Godot Dictionary literal from String keys to raw values."""
    if not entries:
        return "{}"
    return "{\n" + ",\n".join(f"{quote(k)}: {v}" for k, v in entries.items()) + "\n}"


def dictionary_keys(raw: str) -> dict[str, str]:
    """String keys of a Dictionary literal and their raw values."""
    return {
        match.group(1): match.group(2).strip()
        for match in re.finditer(r'"([^"]+)"\s*:\s*([^,\n}]+(?:\([^)]*\))?)', raw)
    }


# --- project.godot ---------------------------------------------------------------------


def get_setting(text: str, section: str, key: str) -> str:
    current = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            current = stripped[1:-1]
        elif current == section and stripped.startswith(key + "="):
            return stripped[len(key) + 1 :]
    return ""


def set_setting(text: str, section: str, key: str, value: Any) -> str:
    """Set ``[section] key=value`` in project.godot text, keeping every other line."""
    raw = value if isinstance(value, str) and value[:1] in '"&[{' else str(value)
    lines = text.splitlines()
    start = next((i for i, l in enumerate(lines) if l.strip() == f"[{section}]"), None)
    if start is None:
        while lines and not lines[-1].strip():
            lines.pop()
        lines += ["", f"[{section}]", "", f"{key}={raw}"]
        return "\n".join(lines) + "\n"
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].startswith("[")),
        len(lines),
    )
    for index in range(start + 1, end):
        if lines[index].startswith(key + "="):
            lines[index] = f"{key}={raw}"
            return "\n".join(lines) + "\n"
    insert = end
    while insert > start + 1 and not lines[insert - 1].strip():
        insert -= 1
    if insert == start + 1:
        lines[insert:insert] = ["", f"{key}={raw}"]
    else:
        lines[insert:insert] = [f"{key}={raw}"]
    return "\n".join(lines) + "\n"
