"""Creation-only rules applied to tool calls, commands and URLs.

A refusal carries a severity, because the two cases deserve different handling:

* ``STOP`` — a Rule Zero violation: something tried to *generate* an asset. The
  whole turn halts and the user is told, rather than the agent quietly probing
  for another route. Everything in ``policy/denylist.toml`` is of this kind:
  generative services and asset sites.
* ``DENY`` — not available here, which is a configuration state, not a
  violation: an MCP server the project did not enable, or a narrow escape-hatch
  tool on a server that is otherwise fine. The call is refused and the agent is
  free to pick another path.

No tool is disabled or removed; everything stays callable and is judged per call.
"""

from __future__ import annotations

import re
import shlex
import tomllib
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlparse

from joinery.catalog import ROOT

DENYLIST_PATH = ROOT / "policy" / "denylist.toml"


class Severity(StrEnum):
    """How hard a refusal lands."""

    STOP = "stop"  # Rule Zero violation: halt the turn and tell the user.
    DENY = "deny"  # Not available here: refuse the call, the turn continues.


@dataclass(frozen=True)
class Verdict:
    """A refusal: why, and how hard."""

    severity: Severity
    reason: str

    def __str__(self) -> str:
        return self.reason


_SEGMENTS = re.compile(r"&&|\|\||[;|\n]")
_INSTALLERS = re.compile(
    r"^(?:\S+=\S+\s+)*(?:sudo\s+)?"
    r"(?:(?:python3?\s+-m\s+)?pip3?\s+install"
    r"|uv\s+(?:add|pip\s+install|tool\s+(?:install|run))|uvx"
    r"|npm\s+(?:i|install|add)|pnpm\s+(?:i|install|add)|yarn\s+add|npx"
    r"|pipx\s+(?:install|run)|brew\s+install|conda\s+install)\b"
)
_VERSION_SUFFIX = re.compile(r"(?<=.)(?:[=<>!~]=?|@).*$")


def normalise(package: str) -> str:
    return re.sub(r"[-_.]+", "-", package).lower()


@dataclass(frozen=True)
class Denylist:
    packages: frozenset[str]
    hosts: tuple[str, ...]


def load_denylist(path: Path = DENYLIST_PATH) -> Denylist:
    with path.open("rb") as f:
        raw = tomllib.load(f)
    return Denylist(
        packages=frozenset(normalise(p) for p in raw["packages"]),
        hosts=tuple(h.lower() for h in raw["hosts"]),
    )


def _host_matches(host: str, denied: str) -> bool:
    return host == denied or host.endswith("." + denied)


def generative_names(denylist: Denylist) -> frozenset[str]:
    """Names that identify a generative service, for judging MCP server names.

    Built from the denylist: package names as written, and the leading label of
    each denied host (``meshy.ai`` -> ``meshy``). Matching is exact, so an
    unrecognised name reads as an ordinary ``DENY`` rather than a false alarm;
    the sandbox and the CI asset gate are the backstop for that.
    """
    names = set(denylist.packages)
    for host in denylist.hosts:
        names.add(normalise(host.split(".", 1)[0]))
    return frozenset(names)


#: `joinery timeline restore` and `start` are the user's own choices in the
#: Timeline tab: an agent asks for a change and the user reviews it instead.
_USER_ONLY = re.compile(r"\bjoinery\b(?:\s+\S+)*?\s+timeline\s+(?:restore|start)\b")


#: Adding reference images and design documents records them as the user's own
#: import (`human-import`), so only the user does it: in the new-project wizard
#: or Project apps (/setup), never an agent's shell command.
_USER_IMPORT = re.compile(
    r"\bjoinery\b(?:\s+\S+)*?\s+(?:references\s+add\b|.*\s--(?:reference|design-doc)\b)"
)


def user_only_violation(command: str) -> Verdict | None:
    """Refuse commands only the user runs, from the editor."""
    for segment in _SEGMENTS.split(command):
        if _USER_ONLY.search(segment):
            return Verdict(
                Severity.DENY,
                "Restoring a file from the Timeline, or starting one, is the user's "
                "choice in the Timeline tab. Ask the user, or make the change as a "
                "reviewed edit.",
            )
        if _USER_IMPORT.search(segment):
            return Verdict(
                Severity.DENY,
                "Only the user adds reference images and design documents, from "
                "Project apps (/setup): they are recorded as the user's own import. "
                "Ask the user to add the file there.",
            )
    return None


def url_violation(url: str, denylist: Denylist) -> Verdict | None:
    host = (urlparse(url).hostname or "").lower()
    for denied in denylist.hosts:
        if _host_matches(host, denied):
            return Verdict(Severity.STOP, f"{denied} is a blocked generative host")
    return None


def command_violation(command: str, denylist: Denylist) -> Verdict | None:
    lowered = command.lower()
    for denied in denylist.hosts:
        if re.search(rf"(?<![\w-]){re.escape(denied)}(?![\w-])", lowered):
            return Verdict(Severity.STOP, f"{denied} is a blocked generative host")
    for segment in _SEGMENTS.split(command):
        segment = segment.strip()
        if not _INSTALLERS.match(segment):
            continue
        try:
            tokens = shlex.split(segment)
        except ValueError:
            tokens = segment.split()
        for token in tokens:
            name = normalise(_VERSION_SUFFIX.sub("", token))
            if name in denylist.packages:
                return Verdict(Severity.STOP, f"{name} is a denied generative package")
    return None
