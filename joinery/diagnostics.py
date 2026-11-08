"""Build and recovery diagnostics without credentials or full private logs."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from joinery.bootstrap import addon_version, transactions
from joinery.connection import all_statuses
from joinery.distribution import engine_binary


def report(project: Path) -> dict[str, Any]:
    binary = engine_binary()
    version = subprocess.run(
        [str(binary), "--version"], capture_output=True, text=True, check=True
    ).stdout.strip()
    data: dict[str, Any] = {
        "engine": version,
        "engine_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "addon_protocol": 2,
        "source_addon": addon_version(),
        "installed_addon": addon_version(project / "addons/joinery")
        if (project / "addons/joinery").is_dir()
        else "missing",
        "providers": all_statuses(),
        "transactions": transactions(project, "list"),
    }
    uuid = ""
    if shutil.which("dwarfdump"):
        result = subprocess.run(
            ["dwarfdump", "--uuid", str(binary)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            uuid = result.stdout.split()[1].lower()
            data["binary_uuid"] = uuid
    reports = sorted(
        (Path.home() / "Library/Logs/DiagnosticReports").glob("godot*.ips"),
        key=lambda path: path.stat().st_mtime,
    )
    if reports:
        try:
            first, rest = reports[-1].read_text().split("\n", 1)
            header = json.loads(first)
            crash = json.loads(rest)
            crash_uuid = str(header.get("slice_uuid", "")).lower()
            data["latest_crash"] = {
                "time": header.get("timestamp"),
                "incident": header.get("incident_id"),
                "binary_uuid": crash_uuid,
                "matches_current_build": bool(uuid and crash_uuid == uuid),
                "exception": crash.get("exception", {}).get("type", ""),
            }
        except (OSError, ValueError, KeyError, IndexError):
            data["latest_crash"] = {"state": "report format not recognized"}
    return data
