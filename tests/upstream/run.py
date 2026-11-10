"""Run application-specific tests against exact catalog MCP source revisions."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from joinery.catalog import ROOT, load_catalog
from joinery.deps import check_deps
from joinery.mcp_runtime import host_environment
from joinery.policy import load_denylist
from tests.upstream.plan import DEFAULT_PROFILE, PROFILES, PROGRAMS
from tests.upstream.sources import prepare


def cases(tool: str, source: Path) -> tuple[list[str], list[str]]:
    """Which upstream tests run, and what is deliberately left out."""
    return ["tests"], []


def junit(path: Path) -> dict[str, int]:
    root = ET.parse(path).getroot()
    nodes = list(root.iter("testcase"))
    failed = sum(
        node.find("failure") is not None or node.find("error") is not None
        for node in nodes
    )
    skipped = sum(node.find("skipped") is not None for node in nodes)
    return {
        "passed": len(nodes) - failed - skipped,
        "failed": failed,
        "skipped": skipped,
    }


def run(tool_id: str, output: Path, audits: Path | None = None) -> dict[str, Any]:
    tool = load_catalog().get(tool_id)
    assert tool.mcp is not None
    output.mkdir(parents=True, exist_ok=True)
    log = output / (tool_id + ".log")
    report: dict[str, Any] = {
        "program": tool_id,
        "server": tool.mcp.server,
        "revision": tool.mcp.ref,
        "scope": "real upstream MCP code with simulated application boundary",
        "workflows": PROGRAMS[tool_id].workflows,
    }
    local = audits / tool_id if audits is not None else None
    with tempfile.TemporaryDirectory(
        prefix="joinery-upstream-" + tool_id + "-"
    ) as directory:
        source = prepare(
            tool.mcp.repo,
            tool.mcp.ref,
            Path(directory) / "source",
            local,
        )
        problems = check_deps(source, load_denylist())
        if problems:
            raise ValueError(
                "Pinned MCP fails dependency policy: " + "; ".join(problems)
            )
        arguments, omitted = cases(tool_id, source)
        environment = host_environment()
        module_paths = [source / "src", source, source / "tests"]
        environment["PYTHONPATH"] = os.pathsep.join(str(path) for path in module_paths)
        environment["PYTHONUTF8"] = "1"
        profile = ROOT / "tests/upstream" / PROFILES.get(tool_id, DEFAULT_PROFILE)
        xml = output / (tool_id + ".xml")
        xml.unlink(missing_ok=True)
        command = [
            "uv",
            "run",
            "--frozen",
            "--project",
            str(profile),
            "python",
            "-m",
            "pytest",
            "-q",
            "--rootdir=" + str(source),
            "-o",
            "asyncio_default_fixture_loop_scope=function",
            "-o",
            "asyncio_default_test_loop_scope=function",
            "--junitxml=" + str(xml),
            *arguments,
        ]
        try:
            result = subprocess.run(
                command,
                cwd=source,
                env=environment,
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )
            log.write_text(result.stdout + "\n" + result.stderr)
            report.update(
                junit(xml) if xml.exists() else {"passed": 0, "failed": 1, "skipped": 0}
            )
            report["status"] = (
                "passed"
                if result.returncode == 0
                and report["failed"] == 0
                and report["passed"] > 0
                else "failed"
            )
            report["exit_code"] = result.returncode
        except subprocess.TimeoutExpired:
            log.write_text("Upstream test process exceeded the 300-second deadline.\n")
            report.update(status="failed", passed=0, failed=1, skipped=0)
        report["boundaries"] = omitted
    report["log"] = str(log)
    (output / (tool_id + ".json")).write_text(json.dumps(report, indent=2) + "\n")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tool", help="catalog tool id with a community MCP")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--local-audits", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "out/upstream-mcp-tests")
    args = parser.parse_args()
    if not args.all and args.tool is None:
        parser.error("Choose --all or --tool.")
    selected = sorted(PROFILES) if args.all else [args.tool]
    reports = []
    for name in selected:
        report = run(name, args.output.resolve(), args.local_audits)
        reports.append(report)
        print(json.dumps(report), flush=True)
    return int(any(report["status"] != "passed" for report in reports))


if __name__ == "__main__":
    raise SystemExit(main())
