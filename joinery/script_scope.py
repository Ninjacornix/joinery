"""What a raw script may reach before it runs: the tool's own document, nothing else.

Typed operations come first; raw bpy, Krita/GIMP Python and sclang are the logged
fallback (CLAUDE.md). The offline worker already confines a script (no network,
only the stage mounted), so this is not the security boundary. It is a scope
check: a document operation that opens sockets, starts programs, imports what no
document operation needs, or names files outside the stage is not doing what it
was called for, and is refused with a plain reason before the app starts, so the
agent can rewrite it (ADR 0078). Rule Zero's denylist runs before this and stops
the turn; a scope refusal only denies the one call.

The check is static and errs towards refusing what it can't see: code built at
run time (``exec``, ``eval``, ``.interpret``) is refused because nothing can say
what it reaches.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import Any

#: Raw-code languages by the tool whose ``operate_document`` takes the code.
LANGUAGES = {
    "blender": "python",
    "krita": "python",
    "gimp": "python",
    "supercollider": "sclang",
}

#: Modules a document script may import (the first dotted part).
PYTHON_MODULES = frozenset(
    {
        # Blender
        "bpy",
        "bmesh",
        "mathutils",
        "bpy_extras",
        "bl_math",
        "addon_utils",
        "bl_ext",
        "rigify",
        # Krita and GIMP
        "krita",
        "PyQt5",
        "gi",
        # The standard library's pure parts
        "__future__",
        "array",
        "base64",
        "bisect",
        "collections",
        "colorsys",
        "copy",
        "dataclasses",
        "datetime",
        "decimal",
        "enum",
        "fractions",
        "functools",
        "hashlib",
        "heapq",
        "itertools",
        "json",
        "math",
        "numbers",
        "operator",
        "random",
        "re",
        "statistics",
        "string",
        "struct",
        "textwrap",
        "time",
        "typing",
        "numpy",
    }
)
#: Parts of the allowed modules that reach past the document.
PYTHON_SUBMODULES = frozenset(
    {"PyQt5.QtNetwork", "PyQt5.QtWebKit", "PyQt5.QtWebEngineWidgets"}
)
#: GIMP's introspection libraries a script may take from ``gi.repository``.
GI_LIBRARIES = frozenset({"Gimp", "Gegl", "GObject", "Babl", "GimpUi"})
#: Calls that run code made at run time.
PYTHON_DYNAMIC = frozenset({"exec", "eval", "compile", "__import__"})
#: Python's ways back to the interpreter's internals.
PYTHON_DUNDERS = frozenset(
    {
        "__subclasses__",
        "__globals__",
        "__builtins__",
        "__code__",
        "__loader__",
        "__import__",
        "__getattribute__",
        "__dict__",
        "__bases__",
        "__mro__",
    }
)
#: Blender operators and utilities outside a document edit: the network, add-on
#: and extension installs, running other script files, user preferences.
BLENDER_CALLS = (
    "bpy.ops.wm.url_open",
    "bpy.ops.extensions.",
    "bpy.ops.preferences.",
    "bpy.ops.script.",
    "bpy.ops.text.run_script",
    "bpy.ops.wm.save_userpref",
    "bpy.ops.wm.read_userpref",
    "bpy.utils.execfile",
    "bpy.utils.extension_path_user",
    "bpy.app.handlers",
)
#: A literal path that leaves the stage: absolute, home, a drive, a share, or `..`.
#: Blender's own "//" (next to the .blend) stays inside.
_OUTSIDE = re.compile(
    r"^(?:/(?!/)\S|~|[A-Za-z]:[\\/]|\\\\|file:)|(?:^|[\\/])\.\.(?:[\\/]|$)"
)


@dataclass(frozen=True)
class Issue:
    """One thing a script reaches outside its tool's scope."""

    #: Stable id, ``scope.<what>`` (network, subprocess, import, files, dynamic).
    rule: str
    line: int
    what: str

    def text(self) -> str:
        return f"line {self.line}: {self.what}"


def raw_script(server: str, tool: str, arguments: dict[str, Any]) -> tuple[str, str]:
    """(language, code) when this call runs raw code; ("", "") when it doesn't."""
    code = arguments.get("code")
    if not isinstance(code, str):
        return "", ""
    if tool == "operate_document" and server.startswith("joinery-"):
        return LANGUAGES.get(str(arguments.get("tool", "")), ""), code
    if server == "blender" and tool == "execute_blender_code":
        return "python", code
    if server == "claude-collider" and tool == "cc_execute":
        return "sclang", code
    return "", ""


def check(language: str, code: str) -> list[Issue]:
    """Every out-of-scope reach in ``code``, by line."""
    if language == "python":
        return _python(code)
    if language == "sclang":
        return _sclang(code)
    return []


def refusal(language: str, issues: list[Issue]) -> str:
    """The plain reason a script was not run."""
    name = {"python": "Python", "sclang": "SuperCollider"}.get(language, language)
    listed = "; ".join(issue.text() for issue in issues[:6])
    more = f" (and {len(issues) - 6} more)" if len(issues) > 6 else ""
    return (
        f"Not run: this {name} script reaches outside the document it edits ({listed}"
        f"{more}). A document operation changes its own source and exports, which "
        "Joinery saves and records; it needs no network, other programs or files "
        "outside the project. Prefer a typed operation; otherwise rewrite the "
        "script without these."
    )


# --- Python (bpy, Krita, GIMP) ----------------------------------------------------


def _dotted(node: ast.AST) -> str:
    """``a.b.c`` for a name or attribute chain ("" for anything else)."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return ""


def _module_issue(name: str, line: int) -> Issue | None:
    top = name.split(".")[0]
    if top not in PYTHON_MODULES:
        rule = {
            "socket": "scope.network",
            "urllib": "scope.network",
            "http": "scope.network",
            "requests": "scope.network",
            "ftplib": "scope.network",
            "smtplib": "scope.network",
            "ssl": "scope.network",
            "subprocess": "scope.subprocess",
            "multiprocessing": "scope.subprocess",
            "pty": "scope.subprocess",
            "os": "scope.subprocess",
            "shutil": "scope.files",
            "pathlib": "scope.files",
            "glob": "scope.files",
            "tempfile": "scope.files",
            "importlib": "scope.dynamic",
            "ctypes": "scope.dynamic",
        }.get(top, "scope.import")
        return Issue(
            rule, line, f"imports {name}, which a document operation does not use"
        )
    if any(name == sub or name.startswith(sub + ".") for sub in PYTHON_SUBMODULES):
        return Issue("scope.network", line, f"imports {name} (network)")
    return None


def _python(code: str) -> list[Issue]:
    try:
        tree = ast.parse(code)
    except SyntaxError as error:
        return [
            Issue(
                "scope.syntax",
                error.lineno or 1,
                f"does not parse ({error.msg}), so what it reaches can't be checked",
            )
        ]
    issues: list[Issue] = []
    for node in ast.walk(tree):
        line = getattr(node, "lineno", 1)
        if isinstance(node, ast.Import):
            for alias in node.names:
                issue = _module_issue(alias.name, line)
                if issue:
                    issues.append(issue)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                issues.append(
                    Issue("scope.import", line, "imports from a relative package")
                )
                continue
            module = node.module or ""
            issue = _module_issue(module, line)
            if issue is None:
                for alias in node.names:
                    issue = _module_issue(f"{module}.{alias.name}", line)
                    if (
                        issue is None
                        and module == "gi.repository"
                        and (alias.name not in GI_LIBRARIES)
                    ):
                        issue = Issue(
                            "scope.import",
                            line,
                            f"imports gi.repository.{alias.name}; a GIMP script uses "
                            + ", ".join(sorted(GI_LIBRARIES)),
                        )
                    if issue:
                        break
            if issue:
                issues.append(issue)
        elif isinstance(node, ast.Call):
            name = _dotted(node.func)
            if name in PYTHON_DYNAMIC:
                issues.append(
                    Issue("scope.dynamic", line, f"{name}() runs code made at run time")
                )
            elif any(
                name == call.rstrip(".") or name.startswith(call)
                for call in BLENDER_CALLS
            ):
                issues.append(
                    Issue("scope.blender", line, f"{name} is outside a document edit")
                )
            for value in [*node.args, *(k.value for k in node.keywords)]:
                if (
                    isinstance(value, ast.Constant)
                    and isinstance(value.value, str)
                    and _OUTSIDE.search(value.value)
                ):
                    issues.append(
                        Issue(
                            "scope.files",
                            line,
                            f"names {value.value!r}, outside the project",
                        )
                    )
        elif isinstance(node, ast.Attribute):
            if node.attr in PYTHON_DUNDERS:
                issues.append(
                    Issue(
                        "scope.dynamic",
                        line,
                        f"reaches {node.attr}, Python's internals",
                    )
                )
            elif _dotted(node) == "bpy.app.handlers":
                issues.append(
                    Issue("scope.blender", line, "bpy.app.handlers runs code later")
                )
        elif isinstance(node, ast.Name) and node.id in PYTHON_DUNDERS:
            issues.append(
                Issue("scope.dynamic", line, f"reaches {node.id}, Python's internals")
            )
    return sorted(set(issues), key=lambda issue: (issue.line, issue.rule, issue.what))


# --- SuperCollider ------------------------------------------------------------------

#: (rule, pattern, what) on the code with comments blanked out.
_SCLANG: tuple[tuple[str, re.Pattern[str], str], ...] = tuple(
    (rule, re.compile(pattern), what)
    for rule, pattern, what in (
        (
            "scope.subprocess",
            r"\.(?:unixCmd\w*|systemCmd|runInTerminal|openOS|openTextFile)\b",
            "starts another program",
        ),
        ("scope.subprocess", r"\b(?:Pipe|UnixFILE)\b", "starts another program"),
        (
            "scope.network",
            r"\bNetAddr\s*(?:\.new)?\s*\(\s*\"(?!127\.0\.0\.1\"|localhost\")",
            "sends to another machine",
        ),
        (
            "scope.network",
            r"\b(?:openUDPPort|openPorts|Quarks?|Download|HTTP\w*)\b",
            "reaches the network",
        ),
        (
            "scope.dynamic",
            (
                r"\.(?:interpret|interpretPrint|compileFile|executeFile|loadPaths|loadRelative)\b"
                r"|\"\s*\.\s*load\b"
            ),
            "runs code from text or another file",
        ),
        (
            "scope.files",
            (
                r"\b(?:File\s*\.\s*(?:delete\w*|mkdir|copy|move|rename)|\.deleteFile|"
                r"Platform\s*\.\s*\w*Dir|thisProcess\s*\.\s*platform\s*\.\s*\w*Dir|"
                r"Document\s*\.\s*dir)\b"
            ),
            "reaches files outside the document",
        ),
        ("scope.files", r"\.standardizePath\b", "expands a home path"),
    )
)
#: An OSC command ("/s_new", "/n_set"): one segment, no extension; not a file.
_OSC = re.compile(r"^/[A-Za-z_]\w*$")
#: A string literal, a line comment or a block comment.
_SC_TOKENS = re.compile(r'"(?:[^"\\]|\\.)*"|//[^\n]*|/\*.*?\*/', re.DOTALL)
_SC_STRING = re.compile(r'"(?:[^"\\]|\\.)*"', re.DOTALL)


def _blank(match: re.Match[str]) -> str:
    """The match as spaces, keeping its line breaks (so offsets keep their lines)."""
    return re.sub(r"[^\n]", " ", match.group(0))


def _sclang(code: str) -> list[Issue]:
    # Comments go (keeping their lines). Strings stay for the path and NetAddr
    # checks, but are blanked for the call patterns: "Pipe" in a message is fine.
    code = _SC_TOKENS.sub(
        lambda match: (
            match.group(0) if match.group(0).startswith('"') else _blank(match)
        ),
        code,
    )
    plain = _SC_STRING.sub(lambda match: '"' + _blank(match)[1:-1] + '"', code)
    issues: list[Issue] = []

    def line(offset: int) -> int:
        return code.count("\n", 0, offset) + 1

    for rule, pattern, what in _SCLANG:
        # The NetAddr pattern reads the host string; the others never look in strings.
        source = code if "NetAddr" in pattern.pattern else plain
        for match in pattern.finditer(source):
            issues.append(
                Issue(rule, line(match.start()), f"{what} ({match.group(0).strip()})")
            )
    for match in _SC_STRING.finditer(code):
        text = match.group(0)[1:-1]
        if _OUTSIDE.search(text) and not _OSC.match(text):
            issues.append(
                Issue(
                    "scope.files",
                    line(match.start()),
                    f"names {text!r}, outside the project",
                )
            )
    return sorted(set(issues), key=lambda issue: (issue.line, issue.rule, issue.what))
