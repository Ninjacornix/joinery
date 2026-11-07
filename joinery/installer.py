"""Install menu: choose creation tools. Any front end can drive these functions."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from pathlib import Path

from joinery.catalog import CONFIG_PATH, Catalog, Tool

DOMAIN_ORDER = {"engine": 0, "3d": 1, "materials": 2, "2d": 3, "audio": 4}
_ENABLED_LINE = re.compile(r"^enabled\s*=\s*\[.*?\]", re.MULTILINE | re.DOTALL)


def menu_tools(catalog: Catalog) -> list[Tool]:
    return sorted(catalog.tool, key=lambda t: (DOMAIN_ORDER[t.domain], t.name))


def validate_choice(catalog: Catalog, chosen: Iterable[str]) -> list[str]:
    """Return the final selection, adding required tools. Raises on invalid ids."""
    picked = set(chosen)
    for tool_id in picked:
        if catalog.get(tool_id).status != "available":
            raise ValueError(f"{tool_id} has no vetted MCP yet")
    return sorted(picked | set(catalog.required_ids()))


def write_selection(selection: list[str], config: Path = CONFIG_PATH) -> None:
    line = "enabled = [" + ", ".join(f'"{t}"' for t in selection) + "]"
    text = config.read_text()
    if _ENABLED_LINE.search(text):
        config.write_text(_ENABLED_LINE.sub(line, text, count=1))
    else:
        config.write_text(text.rstrip("\n") + f"\n\n[tools]\n{line}\n")


def _describe(tool: Tool) -> str:
    if tool.mcp is None:
        reason = (
            "own MCP planned"
            if tool.status == "first-party-planned"
            else "no vetted MCP yet"
        )
        return f"unavailable: {reason}"
    source = f"{tool.mcp.tier} MCP, {tool.mcp.stars} stars"
    return f"{source}, paid tool" if tool.paid else source


def render(catalog: Catalog, selection: set[str]) -> str:
    lines: list[str] = []
    for number, tool in enumerate(menu_tools(catalog), 1):
        if tool.required:
            mark = "[*]"
        elif tool.status != "available":
            mark = "[-]"
        else:
            mark = "[x]" if tool.id in selection else "[ ]"
        lines.append(
            f"{number:>3} {mark} {tool.name:<15} {tool.domain:<10} {_describe(tool)}"
        )
    return "\n".join(lines)


def run_menu(
    catalog: Catalog,
    current: Iterable[str],
    ask: Callable[[str], str] = input,
    show: Callable[[str], None] = print,
) -> list[str]:
    """Toggle tools by number until the user presses Enter. Returns the selection."""
    selection = set(current) | set(catalog.required_ids())
    tools = menu_tools(catalog)
    while True:
        show(render(catalog, selection))
        answer = ask("Toggle numbers (e.g. 2 5), Enter to save: ").strip()
        if not answer:
            return validate_choice(catalog, selection)
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
