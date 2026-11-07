"""Creation-only rules applied to tool calls, commands and URLs."""

from __future__ import annotations

import re
import shlex
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from joinery.catalog import ROOT

DENYLIST_PATH = ROOT / "policy" / "denylist.toml"

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


def url_violation(url: str, denylist: Denylist) -> str | None:
    host = (urlparse(url).hostname or "").lower()
    for denied in denylist.hosts:
        if _host_matches(host, denied):
            return f"{denied} is a blocked host"
    return None


def command_violation(command: str, denylist: Denylist) -> str | None:
    lowered = command.lower()
    for denied in denylist.hosts:
        if re.search(rf"(?<![\w-]){re.escape(denied)}(?![\w-])", lowered):
            return f"{denied} is a blocked host"
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
                return f"{name} is a denied package"
    return None
