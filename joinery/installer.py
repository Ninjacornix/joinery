"""Project tool setup. Any front end, including the Godot create-project screen, uses this."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from joinery.catalog import Catalog, CustomTool, ProjectTools, Tool, selection_ids

DOMAIN_ORDER = {"engine": 0, "3d": 1, "materials": 2, "2d": 3, "audio": 4}
_ENABLED_LINE = re.compile(r"^enabled\s*=\s*\[.*?\]", re.MULTILINE | re.DOTALL)


@dataclass(frozen=True)
class Selection:
    tools: list[str]
    custom: list[CustomTool]


def menu_tools(catalog: Catalog) -> list[Tool]:
    return sorted(catalog.tool, key=lambda t: (DOMAIN_ORDER[t.domain], t.name))


def validate_choice(
    catalog: Catalog, chosen: Iterable[str], custom: Iterable[CustomTool] = ()
) -> list[str]:
    """Return the final selection, adding required tools. Raises on invalid ids."""
    return selection_ids(
        catalog, ProjectTools(enabled=tuple(chosen), custom=tuple(custom))
    )


def _toml_list(values: Iterable[str]) -> str:
    return "[" + ", ".join(json.dumps(v) for v in values) + "]"


def _custom_block(tool: CustomTool) -> str:
    return (
        "\n[[tools.custom]]\n"
        f"id = {json.dumps(tool.id)}\n"
        f"name = {json.dumps(tool.name)}\n"
        f"domain = {json.dumps(tool.domain)}\n"
        f"server = {json.dumps(tool.server)}\n"
        f"command = {json.dumps(tool.command)}\n"
        f"args = {_toml_list(tool.args)}\n"
    )


def write_selection(selection: Selection, config: Path) -> None:
    """Save choices, replacing all earlier custom tools. Other settings stay."""
    text = config.read_text() if config.is_file() else ""
    text = re.sub(r"\n*\[\[tools\.custom\]\]\n(?:[^\[\n].*\n?)*", "\n", text)
    line = f"enabled = {_toml_list(selection.tools)}"
    if _ENABLED_LINE.search(text):
        text = _ENABLED_LINE.sub(line, text, count=1)
    else:
        text = text.rstrip("\n") + f"\n\n[tools]\n{line}\n"
    text = text.rstrip("\n") + "\n"
    config.write_text(text + "".join(_custom_block(c) for c in selection.custom))


def write_mcp_config(selection: Selection, project: Path) -> None:
    """Register custom MCPs, whose commands are known. Other entries are kept."""
    path = project / ".mcp.json"
    data = json.loads(path.read_text()) if path.is_file() else {"mcpServers": {}}
    for tool in selection.custom:
        data["mcpServers"][tool.server] = {
            "command": tool.command,
            "args": list(tool.args),
        }
    if data["mcpServers"]:
        path.write_text(json.dumps(data, indent=2) + "\n")


def _describe(tool: Tool) -> str:
    if tool.builtin_server:
        return "Joinery native document adapter, offline worker"
    if tool.mcp is None:
        reason = (
            "own MCP in progress"
            if tool.status == "first-party-planned"
            else "no vetted MCP"
        )
        return f"unavailable: {reason}"
    source = f"{tool.mcp.tier} MCP, {tool.mcp.stars} stars"
    return f"{source}, paid tool" if tool.paid else source


def render(catalog: Catalog, tools: set[str], custom: Iterable[CustomTool] = ()) -> str:
    lines = ["Preferred tools: non-generative, no telemetry"]
    for number, tool in enumerate(menu_tools(catalog), 1):
        if tool.required and tool.id in tools:
            mark = "[*]"
        elif tool.status != "available":
            mark = "[-]"
        else:
            mark = "[x]" if tool.id in tools else "[ ]"
        domains = "/".join((tool.domain, *tool.also_domains))
        lines.append(
            f"{number:>3} {mark} {tool.name:<15} {domains:<13} {_describe(tool)}"
        )
    custom = list(custom)
    if custom:
        lines.append(
            "Your tools: not vetted, may send telemetry, assets labelled custom-tool"
        )
        lines += [f"    [+] {c.name:<15} {c.domain:<13} {c.command}" for c in custom]
    return "\n".join(lines)


def _ask_custom(ask: Callable[[str], str]) -> CustomTool:
    name = ask("Program name: ").strip()
    return CustomTool(
        id=re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"),
        name=name,
        domain=ask("Domain (engine, 3d, materials, 2d, audio): ").strip(),  # type: ignore[arg-type]
        server=ask("MCP server name: ").strip(),
        command=ask("MCP command: ").strip(),
        args=tuple(ask("MCP arguments: ").split()),
    )


def run_menu(
    catalog: Catalog,
    current: Iterable[str],
    custom: Iterable[CustomTool] = (),
    ask: Callable[[str], str] = input,
    show: Callable[[str], None] = print,
) -> Selection:
    """Toggle tools by number, `a` adds your own, Enter saves."""
    extra = list(custom)
    selection = set(validate_choice(catalog, current, extra))
    tools = menu_tools(catalog)
    while True:
        show(render(catalog, selection, extra))
        answer = ask("Toggle numbers (2 5), a = add your own, Enter = save: ").strip()
        if not answer:
            return Selection(validate_choice(catalog, selection, extra), extra)
        if answer.lower() == "a":
            try:
                added = _ask_custom(ask)
                validate_choice(catalog, selection, [*extra, added])
            except ValueError as exc:
                show(f"Not added: {exc}")
            else:
                extra.append(added)
            continue
        for token in answer.replace(",", " ").split():
            if not token.isdigit() or not 1 <= int(token) <= len(tools):
                show(f"Unknown choice: {token}")
                continue
            tool = tools[int(token) - 1]
            if tool.required:
                show(f"{tool.name} is required.")
            elif tool.status != "available":
                show(f"{tool.name}: {_describe(tool)}.")
            else:
                selection.symmetric_difference_update({tool.id})


def _repo_label(tool: Tool) -> str:
    """`owner/name` of the MCP's repo, which is what identifies it to a user."""
    if tool.mcp is None:
        return ""
    return "/".join(tool.mcp.repo.rstrip("/").split("/")[-2:])


def _mcp_options(tool: Tool, catalog: Catalog) -> list[dict[str, object]]:
    """Every MCP the catalog knows for this tool, the chosen one first.

    Alternatives remain selectable as project-owned custom MCPs. They are not
    promoted into the preferred catalog; their launch command must be supplied
    explicitly before setup can register them.
    """
    options: list[dict[str, object]] = []
    if tool.builtin_server:
        options.append({"label": tool.builtin_server, "state": "chosen", "usable": True, "summary": "in use", "reason": "First-party native document adapter"})
    if tool.mcp is not None:
        options.append(
            {
                "label": tool.mcp.server,
                "state": "chosen",
                "usable": True,
                "summary": "in use",
                "reason": f"{tool.mcp.tier} MCP, pinned to {tool.mcp.ref[:12]}",
            }
        )
    else:
        for candidate in tool.candidates:
            repo, _, why = candidate.partition(":")
            options.append(
                {
                    "label": repo.strip(),
                    "state": "candidate",
                    "usable": True,
                    "custom": _custom_option(tool, repo.strip()),
                    "summary": "not vetted yet",
                    "reason": why.strip() or "not vetted yet",
                }
            )
    for rejected in catalog.rejected_for(tool.id):
        parts = rejected.repo.rstrip("/").split("/")
        options.append(
            {
                "label": "/".join(parts[-2:]),
                "state": "alternative",
                "usable": True,
                "custom": _custom_option(tool, "/".join(parts[-2:])),
                "summary": rejected.summary or "does not meet the bar",
                "reason": rejected.reason,
            }
        )
    return options


def _custom_option(tool: Tool, repo: str) -> dict[str, object]:
    identity = re.sub(r"[^a-z0-9-]+", "-", repo.lower()).strip("-")
    return {
        "id": identity,
        "name": f"{tool.name} ({repo})",
        "domain": tool.domain,
        "server": identity,
        "command": "",
        "args": [],
    }


def custom_choices(payload: str, current: Iterable[CustomTool]) -> list[CustomTool]:
    """Merge explicit UI choices before writing any files or adding required tools."""
    data = json.loads(payload)
    if not isinstance(data, list):
        raise TypeError("custom MCP choices must be a JSON array")
    choices = [CustomTool.model_validate(entry) for entry in data]
    if any(not tool.command.strip() for tool in choices):
        raise ValueError("each custom MCP needs a launch command")
    if len({tool.id for tool in choices}) != len(choices):
        raise ValueError("duplicate custom MCP choice")
    merged = {tool.id: tool for tool in current}
    merged.update({tool.id: tool for tool in choices})
    return list(merged.values())


def tool_menu(catalog: Catalog, current: ProjectTools) -> dict[str, object]:
    """The tool menu as plain data, for a UI that draws its own widgets.

    The Project Manager screen in the fork builds its questionnaire from this,
    so the catalog and its admission rules stay the one source of truth. A UI
    cannot invent a tool, and cannot present an unavailable one as selectable,
    because `available` and `reason` come from the same validation the CLI uses.
    """
    enabled = set(selection_ids(catalog, current))
    entries: list[dict[str, object]] = []
    for tool in menu_tools(catalog):
        entries.append(
            {
                "id": tool.id,
                "name": tool.name,
                "domains": "/".join((tool.domain, *tool.also_domains)),
                "required": tool.required,
                "paid": tool.paid,
                "available": tool.status == "available",
                "enabled": tool.id in enabled,
                "reason": _describe(tool),
                # Which MCP drives the tool, so the menu can say what it is
                # actually wiring up rather than just naming a program.
                "mcp": tool.mcp.server if tool.mcp else tool.builtin_server,
                "mcp_repo": _repo_label(tool),
                # Every MCP the catalog knows for this tool. One entry when a
                # server is vetted; the rejected or still-being-judged
                # candidates when none is, so the menu can show what was
                # considered instead of a bare "none".
                "mcp_options": _mcp_options(tool, catalog),
                # Starred in the menu. The whole catalog is vetted, so this
                # marks the strongest tier: an official MCP, or the engine
                # itself, which every project needs.
                "recommended": bool(
                    tool.required or (tool.mcp and tool.mcp.tier == "official")
                ),
            }
        )
    return {
        "tools": entries,
        "enabled": sorted(enabled),
        "custom": [
            {"id": c.id, "name": c.name, "domain": c.domain} for c in current.custom
        ],
    }
