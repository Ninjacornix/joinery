"""Release and secret-scan workflow contracts.

The release version script is embedded in two workflows; these tests run the
exact embedded text so a tag, the package version and the archive names stay
in step.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def _workflow(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def _version_script(name: str) -> str:
    bodies = [
        re.split(r"\n\s*PY\n", chunk, maxsplit=1)[0]
        for chunk in _workflow(name).split("<<'PY'\n")[1:]
    ]
    scripts = [body for body in bodies if "import tomllib" in body]
    assert len(scripts) == 1, f"{name} must hold exactly one release version script"
    lines = scripts[0].splitlines()
    indent = min(len(line) - len(line.lstrip()) for line in lines if line.strip())
    return "\n".join(line[indent:] for line in lines) + "\n"


def _run_version(
    tmp_path: Path, version: str, ref_type: str = "branch", ref_name: str = "main"
) -> subprocess.CompletedProcess[str]:
    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nname = "joinery"\nversion = "{version}"\n', encoding="utf-8"
    )
    output = tmp_path / "github_output"
    output.write_text("", encoding="utf-8")
    env = {
        key: value for key, value in os.environ.items() if not key.startswith("GITHUB_")
    }
    env |= {
        "GITHUB_REF_TYPE": ref_type,
        "GITHUB_REF_NAME": ref_name,
        "GITHUB_OUTPUT": str(output),
    }
    return subprocess.run(
        [sys.executable, "-I", "-"],
        input=_version_script("release.yml"),
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_both_workflows_share_one_version_script() -> None:
    assert _version_script("release.yml") == _version_script("native.yml")


@pytest.mark.parametrize(
    ("version", "tag_version", "prerelease"),
    [
        ("0.2.0", "0.2.0", "false"),
        ("0.2.0a1", "0.2.0-alpha.1", "true"),
        ("1.0.0b2", "1.0.0-beta.2", "true"),
        ("1.0.0rc1", "1.0.0-rc.1", "true"),
    ],
)
def test_tag_must_equal_package_version(
    tmp_path: Path, version: str, tag_version: str, prerelease: str
) -> None:
    result = _run_version(tmp_path, version, "tag", f"v{tag_version}")
    assert result.returncode == 0, result.stderr
    outputs = (tmp_path / "github_output").read_text(encoding="utf-8").splitlines()
    assert outputs == [f"version={tag_version}", f"prerelease={prerelease}"]

    mismatch = _run_version(tmp_path, version, "tag", "v9.9.9")
    assert mismatch.returncode != 0
    assert "does not match package version" in mismatch.stderr


@pytest.mark.parametrize("version", ["0.2", "0.2.0.dev1", "0.2.0.post1", "0.2.0+local"])
def test_non_release_versions_are_refused(tmp_path: Path, version: str) -> None:
    result = _run_version(tmp_path, version)
    assert result.returncode != 0
    assert "is not a release version" in result.stderr


def test_branch_runs_do_not_need_a_tag(tmp_path: Path) -> None:
    assert _run_version(tmp_path, "0.2.0", "branch", "release/desktop").returncode == 0


def test_current_package_version_is_releasable(tmp_path: Path) -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert _run_version(tmp_path, project["project"]["version"]).returncode == 0


def test_release_names_come_from_the_version() -> None:
    release = _workflow("release.yml")
    native = _workflow("native.yml")
    for text in (release, native):
        assert not re.search(r"Joinery-\d", text), (
            "archive names must not hard-code a version"
        )
    assert "'v[0-9]+.[0-9]+.[0-9]+'" in release
    assert "'v[0-9]+.[0-9]+.[0-9]+-*'" in release
    assert "Joinery-${{ steps.release_version.outputs.version }}-" in native
    # Only suffixed versions are pre-releases.
    assert (
        'if [ "$PRERELEASE" = true ]; then\n            flags+=(--prerelease)'
        in release
    )
    assert release.count("--prerelease") == 1


def test_secret_scan_uses_a_verified_pinned_binary() -> None:
    ci = _workflow("ci.yml")
    assert "\n  secrets:\n" in ci
    assert "GITLEAKS_VERSION: 8.30.1" in ci
    assert re.search(r"GITLEAKS_SHA256: [0-9a-f]{64}\n", ci)
    assert "sha256sum --check --strict" in ci
    assert "fetch-depth: 0" in ci
    assert "gitleaks-action" not in ci
    assert "git . --config .gitleaks.toml" in ci


def test_secret_rules_allowlist_only_the_upstream_checkout() -> None:
    config = tomllib.loads((ROOT / ".gitleaks.toml").read_text(encoding="utf-8"))
    assert config["extend"] == {"useDefault": True}
    assert [entry["paths"] for entry in config["allowlists"]] == [
        ["^godot_patches/godot-src/"]
    ]
    assert "allowlist" not in config
    assert all(set(entry) <= {"description", "paths"} for entry in config["allowlists"])


@pytest.mark.parametrize("name", ["ci.yml", "release.yml", "native.yml"])
def test_actions_are_pinned_by_commit(name: str) -> None:
    uses = re.findall(r"uses:\s*(\S+)", _workflow(name))
    assert uses
    for action in uses:
        if action.startswith("./"):
            continue
        assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", action), action


def test_a_push_to_main_runs_the_quick_checks_only() -> None:
    """Pushes to main stay cheap: Linux checks, no workers, no native editors."""
    text = _workflow("ci.yml")
    quick = "(github.event_name == 'push' && github.ref == 'refs/heads/main')"
    assert "paths-ignore:" in text and "'docs/**'" in text
    assert "cancel-in-progress: ${{ github.event_name == 'push'" in text
    assert f"{quick} || github.event_name == 'pull_request') && '[\"ubuntu-24.04\"]'" in text
    # The heavy jobs skip quick runs; a release (workflow_call) still runs them.
    assert text.count(f"!({quick} || github.event_name == 'pull_request')") == 2
    assert "type: choice" in text and "windows-2025" in text
