"""Materialize exact Git-pinned software source for isolated upstream MCP tests.

Source stays outside the game/project and outside Joinery's production MCP cache.
An existing audit checkout may supply Git objects; its editable files are never
trusted or modified. A fresh fetch is explicit and addresses only the catalog pin.
"""

from __future__ import annotations

import io
import subprocess
import tarfile
import tempfile
from pathlib import Path


def prepare(
    repository: str,
    revision: str,
    destination: Path,
    local: Path | None = None,
    *,
    local_test_scores: bool = False,
) -> Path:
    if local_test_scores and (local is None or not (local / ".git/objects").is_dir()):
        raise ValueError(
            "Example score fixtures require an existing local audit, never a download."
        )
    if destination.exists():
        raise ValueError("Use a fresh source destination.")
    if local is not None and not (local / ".git/objects").is_dir():
        raise ValueError("The supplied local audit has no Git object store.")
    with tempfile.TemporaryDirectory(prefix="joinery-mcp-git-") as directory:
        metadata = Path(directory) / "repository.git"
        subprocess.run(
            ["git", "init", "--bare", str(metadata)], check=True, capture_output=True
        )
        local_objects = local / ".git/objects" if local is not None else None
        if local_objects is not None and local_objects.is_dir():
            # Bytes, not text: Windows text mode would write "\r\n", and Git
            # would then read the path with a trailing "\r".
            (metadata / "objects/info/alternates").write_bytes(
                (local_objects.resolve().as_posix() + "\n").encode()
            )
        else:
            subprocess.run(
                [
                    "git",
                    "--git-dir",
                    str(metadata),
                    "fetch",
                    "--depth=1",
                    repository,
                    revision,
                ],
                check=True,
                capture_output=True,
                timeout=120,
            )
        actual = subprocess.run(
            ["git", "--git-dir", str(metadata), "rev-parse", revision + "^{commit}"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        if actual != revision:
            raise ValueError("Upstream MCP source differs from the catalog pin.")
        archive = subprocess.run(
            ["git", "--git-dir", str(metadata), "archive", revision],
            check=True,
            capture_output=True,
            timeout=60,
        ).stdout
    destination.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(archive)) as package:
        for member in package.getmembers():
            # Software/tests only: do not copy example art, audio or archives.
            path = Path(member.name)
            allowed = path.suffix.lower() in {
                ".py",
                ".toml",
                ".lock",
                ".ini",
                ".cfg",
                ".txt",
                ".md",
                ".json",
                ".qml",
                ".js",
                ".ts",
                ".lua",
                ".xml",
                ".mscx",
                ".rpp",
                ".rtracktemplate",
            } or path.name.lower().startswith(("license", "copying"))
            if (
                local_test_scores
                and path.suffix.lower() == ".mscz"
                and "examples" in path.parts
            ):
                allowed = True  # Existing software test fixtures, outside every game.
            if not allowed:
                continue
            if not member.isfile() or path.is_absolute() or ".." in path.parts:
                if member.isdir():
                    continue
                raise ValueError(
                    "Unsafe upstream source archive member: " + member.name
                )
            payload = package.extractfile(member)
            assert payload is not None
            target = destination / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload.read())
    (destination / ".joinery-test-revision").write_text(revision + "\n")
    return destination
