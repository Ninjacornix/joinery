"""Inkscape: self-contained SVG sources exported to PNG in the offline worker.

Persistent SVG edits go through Joinery's `operate_vector` (a whole document)
or `edit_vector_element` (one element by id: typed attributes, style,
transform, text). The pinned upstream Inkscape MCP is for inspection and
analysis only.
"""

from __future__ import annotations

import io
import re
from xml.etree import ElementTree

from joinery.tools.base import (
    CREATIVE_WORKER,
    INKSCAPE_WORKER,
    Feature,
    ImageCheck,
    InstallCheck,
    McpProbe,
    ToolModule,
    WorkerExec,
)

#: External files, entities, scripts and remote references.
_EXTERNAL = re.compile(
    r"(?:file:|<!ENTITY|<script|(?:href|url)\s*[=(]\s*['\"]?https?:)", re.IGNORECASE
)


#: Where an upstream Inkscape MCP edit has to go to be kept.
KEEP_EDITS = (
    "Files this server writes have no recipe, so review refuses them; make SVG "
    "changes you want to keep with joinery-paint operate_vector."
)


def check_svg(svg: str) -> None:
    """Refuse an SVG that would load anything from outside itself."""
    if _EXTERNAL.search(svg):
        raise ValueError(
            "Vector documents must be self-contained; external assets and scripts are not accepted."
        )


#: An attribute name: plain or with a namespace prefix (inkscape:label).
_ATTRIBUTE = re.compile(r"^(?:([A-Za-z_][\w.-]*):)?([A-Za-z_][\w.-]*)$")
#: A CSS property in a style attribute.
_PROPERTY = re.compile(r"^-?[A-Za-z][A-Za-z0-9-]*$")
_NUMBER = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
#: One SVG transform function and how many numbers it takes.
_TRANSFORMS = {
    "matrix": (6,),
    "translate": (1, 2),
    "scale": (1, 2),
    "rotate": (1, 3),
    "skewX": (1,),
    "skewY": (1,),
}
_FUNCTION = re.compile(r"\s*([A-Za-z]+)\s*\(([^()]*)\)\s*,?")
#: Elements whose text edit_vector_element may set.
TEXT_ELEMENTS = frozenset({"text", "tspan", "textPath", "title", "desc", "flowPara"})
#: Attributes changed through their own fields, or never: an element keeps its id.
_OWN_FIELDS = {"style": "style", "transform": "transform"}
MAX_VALUE = 10_000


def check_transform(value: str) -> str:
    """An SVG transform list, each function with the numbers it takes."""
    text = value.strip()
    position = 0
    while position < len(text):
        match = _FUNCTION.match(text, position)
        if match is None or match.group(1) not in _TRANSFORMS:
            raise ValueError(
                "transform is a list of "
                + ", ".join(f"{name}(…)" for name in _TRANSFORMS)
                + "."
            )
        numbers = [n for n in re.split(r"[\s,]+", match.group(2).strip()) if n]
        if len(numbers) not in _TRANSFORMS[match.group(1)] or not all(
            re.fullmatch(_NUMBER, n) for n in numbers
        ):
            counts = " or ".join(str(c) for c in _TRANSFORMS[match.group(1)])
            raise ValueError(f"{match.group(1)}() takes {counts} numbers.")
        position = match.end()
    return text


def _value(name: str, value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError(f"{name} is text or a number.")  # noqa: TRY004 - shown to the agent
    text = f"{value:g}" if isinstance(value, float) else str(value)
    if len(text) > MAX_VALUE or any(ord(c) < 32 and c not in "\t\n" for c in text):
        raise ValueError(f"{name} is plain text up to {MAX_VALUE} characters.")
    if "javascript:" in text.lower().replace(" ", ""):
        raise ValueError(f"{name} cannot hold a script.")
    for target in re.findall(r"url\(\s*['\"]?([^)'\"]*)", text, re.IGNORECASE):
        if not target.startswith("#"):
            raise ValueError(f"{name} may only refer to this document (url(#id)).")
    return text


def _style(current: str, changes: dict[str, object]) -> str:
    """The style attribute with `changes` applied (None removes a property)."""
    rows: dict[str, str] = {}
    for part in current.split(";"):
        name, colon, old = part.partition(":")
        if colon and name.strip():
            rows[name.strip()] = old.strip()
    for key, value in changes.items():
        if not isinstance(key, str) or not _PROPERTY.match(key):
            raise ValueError(f"{key!r} is not a style property.")
        if value is None:
            rows.pop(key, None)
            continue
        text = _value(key, value)
        if any(c in text for c in ";{}"):
            raise ValueError(f"The {key} value cannot contain ';', '{{' or '}}'.")
        rows[key] = text.strip()
    return ";".join(f"{k}:{v}" for k, v in rows.items())


def _namespaces(svg: str) -> dict[str, str]:
    """Every prefix the document declares -> its namespace."""
    found: dict[str, str] = {}
    for _event, (prefix, uri) in ElementTree.iterparse(
        io.StringIO(svg), events=("start-ns",)
    ):
        found.setdefault(prefix, uri)
    return found


def edit_element(
    svg: str,
    element_id: str,
    *,
    attributes: dict[str, object] | None = None,
    style: dict[str, object] | None = None,
    transform: str | None = None,
    text: str | None = None,
    clear_transform: bool = False,
) -> tuple[str, dict[str, object]]:
    """The document with one element (by id) changed, and what changed on it.

    Attributes set to None are removed. The element keeps its id; everything
    outside the element is written back as it was parsed.
    """
    check_svg(svg)
    if not isinstance(element_id, str) or not element_id.strip():
        raise ValueError("Name the element by its id.")
    if not (
        attributes
        or style
        or transform is not None
        or text is not None
        or clear_transform
    ):
        raise ValueError("Give attributes, style, transform or text to change.")
    try:
        prefixes = _namespaces(svg)
        parser = ElementTree.XMLParser(
            target=ElementTree.TreeBuilder(insert_comments=True, insert_pis=True)
        )
        root = ElementTree.fromstring(svg, parser=parser)
    except ElementTree.ParseError as exc:
        raise ValueError(f"The SVG does not parse: {exc}") from None
    matches = [e for e in root.iter() if e.get("id") == element_id]
    if not matches:
        raise ValueError(f"The SVG has no element with id {element_id!r}.")
    if len(matches) > 1:
        raise ValueError(f"{len(matches)} elements have the id {element_id!r}.")
    element = matches[0]
    changed: dict[str, object] = {}
    for name, value in (attributes or {}).items():
        match = _ATTRIBUTE.match(name) if isinstance(name, str) else None
        if match is None:
            raise ValueError(f"{name!r} is not an attribute name.")
        prefix, local = match.groups()
        if local.lower().startswith("on"):
            raise ValueError(f"{name}: event handlers run scripts; not accepted.")
        if local == "id" and not prefix:
            raise ValueError("The element keeps its id.")
        if not prefix and local in _OWN_FIELDS:
            raise ValueError(f"Change {local} with the {_OWN_FIELDS[local]} field.")
        if prefix:
            if prefix not in prefixes:
                raise ValueError(f"The SVG declares no namespace {prefix!r}.")
            key = "{" + prefixes[prefix] + "}" + local
        else:
            key = local
        if value is None:
            element.attrib.pop(key, None)
        else:
            shown = _value(name, value)
            if local == "href" and not shown.startswith("#"):
                raise ValueError(f"{name} may only point inside this document (#id).")
            element.set(key, shown)
        changed[name] = value
    if style:
        element.set("style", _style(element.get("style", ""), style))
        if not element.get("style"):
            element.attrib.pop("style")
        changed["style"] = style
    if clear_transform:
        element.attrib.pop("transform", None)
        changed["transform"] = None
    elif transform is not None:
        element.set("transform", check_transform(_value("transform", transform)))
        changed["transform"] = element.get("transform")
    if text is not None:
        tag = element.tag.split("}")[-1]
        if tag not in TEXT_ELEMENTS:
            raise ValueError(
                f"{element_id} is a <{tag}>; text goes in "
                + ", ".join(f"<{t}>" for t in sorted(TEXT_ELEMENTS))
                + "."
            )
        if len(element):
            raise ValueError(
                f"{element_id} holds other elements; change the text of the "
                "<tspan> inside it by its id."
            )
        element.text = _value("text", text)
        changed["text"] = text
    head = re.search(r"<(?:[A-Za-z_][\w.-]*:)?svg[\s>/]", svg)
    prolog = svg[: head.start()] if head else ""
    for prefix, uri in prefixes.items():
        ElementTree.register_namespace(prefix, uri)
    body = ElementTree.tostring(root, encoding="unicode")
    edited = prolog + body + ("\n" if svg.endswith("\n") else "")
    check_svg(edited)
    return edited, changed


def vector_code(source: str, svg: str, export: str) -> str:
    """The recipe script: write the SVG, then export the PNG through Inkscape."""
    return (
        "import pathlib, subprocess\n"
        + f"pathlib.Path({source!r}).write_text({svg!r})\n"
        + f"subprocess.run(['inkscape', {source!r}, '--export-filename=' + {export!r}], check=True)\n"
    )


class Inkscape(ToolModule):
    id = "inkscape"
    name = "Inkscape"
    family = "joinery-paint"
    suffix = ".svg"
    authoring = True
    binary = "inkscape"
    desktop = "inkscape"
    ready_summary = "{name} {version} ready (runs offline)"
    broken = "Inkscape failed its check"
    features = (
        Feature(
            "analysis",
            "analysis tools",
            broken="Inkscape analysis tools error",
        ),
    )
    checks = (
        ImageCheck(CREATIVE_WORKER),
        WorkerExec(
            CREATIVE_WORKER,
            ("inkscape", "--version"),
            expect=r"Inkscape \d",
            version=r"Inkscape (\d+\.\d+(?:\.\d+)?)",
            on_failure="needs_image",
            failure="Inkscape is missing from the creative worker; rebuild it",
        ),
        McpProbe(
            "joinery-paint",
            "document_capabilities",
            capability="inkscape",
            image=CREATIVE_WORKER,
        ),
        ImageCheck(INKSCAPE_WORKER, feature="analysis"),
        InstallCheck("inkscape", feature="analysis"),
        McpProbe(
            "inkscape",
            "inkscape_system",
            args=(("operation", "diagnostics"),),
            expect=r'"all_passed":\s*true',
            feature="analysis",
            image=INKSCAPE_WORKER,
        ),
    )

    #: The pinned MCP runs headless: its live tools need the desktop app over
    #: D-Bus (policy blocks them), and files it writes get no recipe.
    mcp_notes = (
        (
            "inkscape_extension",
            "Joinery: run_live needs the Inkscape desktop app and is blocked in the "
            "offline worker; use list, describe or run on a file. " + KEEP_EDITS,
        ),
        ("inkscape_vector", "Joinery: " + KEEP_EDITS),
        ("inkscape_file", "Joinery: " + KEEP_EDITS),
    )

    def replay_argv(self, script: str) -> list[str]:
        return ["python3", script]


MODULE = Inkscape()
