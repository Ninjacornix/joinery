"""Completeness and source-integrity gates for all program/MCP test lanes."""

import json
import subprocess
from pathlib import Path

import pytest

from joinery.catalog import load_catalog
from joinery.hook import mcp_violation
from tests.upstream import run as upstream_runner
from tests.upstream.plan import CORE_SERVERS, PROFILES, PROGRAMS
from tests.upstream.run import junit
from tests.upstream.sources import prepare


def test_every_supporting_program_has_an_explicit_workflow_test_plan() -> None:
    assert set(PROGRAMS) == {tool.id for tool in load_catalog().tool}
    for tool in load_catalog().tool:
        plan = PROGRAMS[tool.id]
        assert plan.workflows
        if tool.mcp:
            assert tool.mcp.server in plan.servers
            assert tool.id in PROFILES or tool.mcp.server in CORE_SERVERS
        elif tool.builtin_server:
            assert tool.builtin_server in plan.servers
        else:
            assert tool.status == "first-party-planned"
            assert plan.native_requirement and not plan.servers


def test_every_preferred_and_first_party_mcp_is_accounted_for() -> None:
    catalog = load_catalog()
    expected = set(catalog.builtin_servers) | {
        tool.mcp.server for tool in catalog.tool if tool.mcp
    }
    actual = {server for plan in PROGRAMS.values() for server in plan.servers}
    assert actual == expected
    assert len(actual) == 10


@pytest.mark.parametrize("program", sorted(PROGRAMS))
def test_program_plan_distinguishes_upstream_contracts_from_native_requirements(
    program: str,
) -> None:
    plan = PROGRAMS[program]
    assert len(set(plan.servers)) == len(plan.servers)
    if program in PROFILES:
        assert plan.native_requirement
        profile = Path(__file__).parent / "upstream" / PROFILES[program]
        assert (profile / "uv.lock").is_file()
        assert (profile / "pyproject.toml").is_file()


@pytest.mark.parametrize(
    "tool",
    [tool for tool in load_catalog().tool if tool.mcp and tool.mcp.blocked_tools],
    ids=lambda tool: tool.id,
)
def test_every_catalog_blocked_method_remains_blocked_in_all_program_test_lanes(
    tmp_path: Path, tool
) -> None:
    (tmp_path / "joinery.toml").write_text(
        "[tools]\nenabled=" + json.dumps([tool.id]) + "\n"
    )
    for method in tool.mcp.blocked_tools:
        verdict = mcp_violation(tool.mcp.server, method, tmp_path)
        assert verdict is not None


@pytest.fixture
def repository(tmp_path: Path) -> tuple[Path, str]:
    source = tmp_path / "existing audited source"
    source.mkdir()
    commands = [
        ["git", "-c", "init.templateDir=", "init", str(source)],
        ["git", "-C", str(source), "add", "."],
        [
            "git",
            "-C",
            str(source),
            "-c",
            "user.name=Joinery Tests",
            "-c",
            "user.email=tests@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-m",
            "Fixture software source",
        ],
    ]
    subprocess.run(commands[0], check=True, capture_output=True)
    (source / "server.py").write_text("PINNED = True\n")
    (source / "LICENSE").write_text("Fixture code only\n")
    (source / "example.png").write_bytes(b"not copied")
    (source / ".env").write_text("PRIVATE_FIXTURE_VALUE=do-not-copy\n")
    (source / "examples").mkdir()
    (source / "examples/local.mscz").write_bytes(
        b"existing local software test fixture"
    )
    for command in commands[1:]:
        subprocess.run(command, check=True, capture_output=True)
    revision = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return source, revision


def test_upstream_tests_use_pinned_git_blobs_not_uncommitted_source_or_private_files(
    repository: tuple[Path, str], tmp_path: Path
) -> None:
    source, revision = repository
    (source / "server.py").write_text("UNCOMMITTED = True\n")
    destination = prepare("not-fetched", revision, tmp_path / "prepared", source)
    assert (destination / "server.py").read_text() == "PINNED = True\n"
    assert (destination / ".joinery-test-revision").read_text().strip() == revision
    assert not (destination / ".env").exists()
    assert not (destination / "example.png").exists()
    assert not (destination / "examples/local.mscz").exists()
    assert (source / "server.py").read_text() == "UNCOMMITTED = True\n"


def test_existing_local_score_fixtures_are_opt_in_and_never_require_asset_download(
    repository: tuple[Path, str], tmp_path: Path
) -> None:
    source, revision = repository
    destination = prepare(
        "not-fetched", revision, tmp_path / "prepared", source, local_test_scores=True
    )
    assert (
        destination / "examples/local.mscz"
    ).read_bytes() == b"existing local software test fixture"
    with pytest.raises(ValueError, match="never a download"):
        prepare("not-fetched", revision, tmp_path / "refused", local_test_scores=True)


def test_invalid_local_audit_does_not_silently_switch_to_network(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="object store"):
        prepare("not-fetched", "0" * 40, tmp_path / "output", tmp_path / "missing")


def test_wrong_source_pin_is_rejected_before_materializing_code(
    repository: tuple[Path, str], tmp_path: Path
) -> None:
    source, _ = repository
    with pytest.raises(subprocess.CalledProcessError):
        prepare("not-fetched", "0" * 40, tmp_path / "wrong", source)
    assert not (tmp_path / "wrong").exists()


def test_junit_counts_failures_skips_and_actual_successes(tmp_path: Path) -> None:
    path = tmp_path / "report.xml"
    path.write_text(
        "<testsuites><testsuite><testcase/><testcase><skipped/></testcase><testcase><failure/></testcase><testcase><error/></testcase></testsuite></testsuites>"
    )
    assert junit(path) == {"passed": 1, "failed": 2, "skipped": 1}


@pytest.mark.parametrize(
    "outcome", ["passed", "skipped", "failed", "crashed", "timeout"]
)
def test_upstream_lane_cannot_report_empty_failed_or_timed_out_work_as_passed(
    tmp_path: Path, monkeypatch, outcome: str
) -> None:
    def source(_repository, _revision, destination, _local, **_options):
        destination.mkdir()
        return destination

    def execute(command, **options):
        assert "--frozen" in command and "--project" in command
        assert options["timeout"] == 300
        assert "OPENAI_API_KEY" not in options["env"]
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(command, 300)
        report = Path(
            next(
                arg.split("=", 1)[1] for arg in command if arg.startswith("--junitxml=")
            )
        )
        node = {
            "passed": "<testcase/>",
            "skipped": "<testcase><skipped/></testcase>",
            "failed": "<testcase><failure/></testcase>",
            "crashed": "",
        }[outcome]
        report.write_text(
            "<testsuites><testsuite>" + node + "</testsuite></testsuites>"
        )
        return subprocess.CompletedProcess(
            command, int(outcome == "crashed"), "fixture log", ""
        )

    monkeypatch.setenv("OPENAI_API_KEY", "test-secret-never-forwarded")
    monkeypatch.setattr(upstream_runner, "prepare", source)
    monkeypatch.setattr(upstream_runner.subprocess, "run", execute)
    report = upstream_runner.run("inkscape", tmp_path / "evidence")
    assert report["status"] == ("passed" if outcome == "passed" else "failed")
    assert json.loads(json.dumps(report)) == json.loads(
        (tmp_path / "evidence/inkscape.json").read_text()
    )
    assert (tmp_path / "evidence/inkscape.log").is_file()
