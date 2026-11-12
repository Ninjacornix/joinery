"""The updater against a local fake release server and fake archives (ADR 0083).

The fake signature check stands in for Sigstore: a bundle is "good" when it names
the archive's digest. The real Sigstore path is covered by a rejection test here
and was checked by hand against a published bundle (see the ADR).
"""

from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys
import tarfile
import threading
import zipfile
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from joinery import update_cli, update_swap, updater

TARGET = "linux-x86_64"


def _archive(version: str, target: str = TARGET, *, marker: str = "new") -> bytes:
    """A tiny release archive with the layout and manifest a real one has."""
    files = {
        "manifest.json": json.dumps(
            {"joinery": updater.package_version(version), "target": target}
        ).encode(),
        "Joinery": b"#!/bin/sh\n",
        "VERSION": marker.encode(),
    }
    if target.startswith("linux-"):
        raw = io.BytesIO()
        with tarfile.open(fileobj=raw, mode="w:gz") as archive:
            for name, data in files.items():
                info = tarfile.TarInfo("joinery/" + name)
                info.size = len(data)
                info.mode = 0o755
                archive.addfile(info, io.BytesIO(data))
        return raw.getvalue()
    root = "Joinery.app/Contents/Resources/joinery/"
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as archive:
        for name, data in files.items():
            archive.writestr(root + name, data)
    return raw.getvalue()


def good_bundle(data: bytes) -> bytes:
    return b"signed:" + hashlib.sha256(data).hexdigest().encode()


def fake_verifier(digest: bytes, bundle: bytes, version: str) -> None:
    if bundle != b"signed:" + digest.hex().encode():
        raise updater.UpdateError("The signature does not match this release.")


class FakeGitHub:
    """Serves /releases and /download/<name> from a dict; can go silent or refuse."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.releases: list[dict[str, Any]] = []
        self.status = 200
        self.requests: list[str] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                fake.requests.append(self.path)
                if fake.status != 200:
                    self.send_error(fake.status)
                    return
                if self.path.startswith("/releases"):
                    body = json.dumps(fake.releases).encode()
                else:
                    name = self.path.rsplit("/", 1)[-1]
                    if name not in fake.files:
                        self.send_error(404)
                        return
                    body = fake.files[name]
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: object) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def publish(
        self,
        version: str,
        *,
        prerelease: bool = False,
        data: bytes | None = None,
        digest: str | None = None,
        bundle: bytes | None = b"",
        target: str = TARGET,
    ) -> bytes:
        data = _archive(version, target) if data is None else data
        name = updater.asset_name(version, target)
        self.files[name] = data
        self.files[name + ".sha256"] = (
            f"{digest or hashlib.sha256(data).hexdigest()}  {name}\n".encode()
        )
        assets = [
            {
                "name": name,
                "size": len(data),
                "browser_download_url": f"{self.url}/download/{name}",
            },
            {
                "name": name + ".sha256",
                "size": 100,
                "browser_download_url": f"{self.url}/download/{name}.sha256",
            },
        ]
        if bundle is not None:
            self.files[name + ".sigstore.json"] = bundle or good_bundle(data)
            assets.append(
                {
                    "name": name + ".sigstore.json",
                    "size": 100,
                    "browser_download_url": f"{self.url}/download/{name}.sigstore.json",
                }
            )
        self.releases.append(
            {
                "tag_name": "v" + version,
                "draft": False,
                "prerelease": prerelease,
                "body": f"## Joinery {version}\n\n- Faster review.",
                "html_url": f"{self.url}/tag/v{version}",
                "assets": assets,
            }
        )
        return data


@pytest.fixture
def github(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[FakeGitHub]:
    fake = FakeGitHub()
    install = tmp_path / "Apps/joinery"
    install.mkdir(parents=True)
    (install / "VERSION").write_text("old")
    monkeypatch.setenv("JOINERY_RELEASES_URL", fake.url + "/releases")
    monkeypatch.setenv("JOINERY_UPDATE_HOME", str(tmp_path / "updates"))
    monkeypatch.setenv("JOINERY_INSTALL_ROOT", str(install))
    monkeypatch.setattr(updater, "platform_target", lambda: TARGET)
    monkeypatch.setattr(updater, "tag_version", lambda version="0.5.0": "0.5.0")
    monkeypatch.setattr(updater, "open_projects", lambda root: [])
    yield fake
    fake.server.shutdown()


def _started(paths: list[Path]) -> Any:
    def start(plan: Path) -> int:
        paths.append(plan)
        return 4242

    return start


def test_versions_order_and_forms() -> None:
    key = updater.version_key
    assert key("0.6.0-alpha.1") < key("0.6.0-beta.1") < key("0.6.0-rc.1") < key("0.6.0")
    assert key("0.6.0a1") == key("v0.6.0-alpha.1")
    assert key("0.10.0") > key("0.9.9")
    assert key("not a version") is None
    assert updater.tag_version("0.6.0rc2") == "0.6.0-rc.2"
    assert updater.package_version("0.6.0-beta.3") == "0.6.0b3"
    assert (
        updater.asset_name("0.6.0", "linux-x86_64")
        == "Joinery-0.6.0-linux-x86_64.tar.gz"
    )
    assert updater.asset_name("0.6.0", "windows-x86_64").endswith(".zip")


def test_check_finds_the_newest_release_once_a_day(github: FakeGitHub) -> None:
    github.publish("0.6.0")
    github.publish("0.5.1")
    github.publish("0.4.0")
    first = updater.check(now=1000.0)
    assert first["available"] and first["release"]["version"] == "0.6.0"
    assert first["release"]["signed"] and "Faster review" in first["release"]["notes"]
    assert first["cached"] is False
    asked = len(github.requests)
    again = updater.check(now=1000.0 + 3600)
    assert again["cached"] and again["release"]["version"] == "0.6.0"
    assert len(github.requests) == asked, "a second check within a day stays local"
    updater.check(now=1000.0 + 25 * 3600)
    assert len(github.requests) == asked + 1
    updater.check(force=True, now=1000.0 + 25 * 3600)
    assert len(github.requests) == asked + 2


def test_prereleases_only_when_opted_in(github: FakeGitHub) -> None:
    github.publish("0.6.0-beta.1", prerelease=True)
    assert not updater.check(force=True)["available"]
    updater.set_prereleases(True)
    result = updater.check()  # The choice clears the daily cache.
    assert result["release"]["version"] == "0.6.0-beta.1" and result["prereleases"]
    assert result["release"]["prerelease"]


def test_offline_and_rate_limited_are_quiet(
    github: FakeGitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    github.status = 403
    limited = updater.check(force=True)
    assert limited["offline"] and not limited["available"]
    monkeypatch.setenv("JOINERY_RELEASES_URL", "http://127.0.0.1:9/releases")
    down = updater.check(force=True)
    assert down["offline"] and "offline" in down["message"].lower()
    assert "last_check" not in updater.load_state(), "offline does not count as a check"


def test_install_verifies_then_stages_and_schedules_the_swap(
    github: FakeGitHub,
) -> None:
    github.publish("0.6.0")
    plans: list[Path] = []
    steps: list[str] = []
    result = updater.install(
        wait_pid=99,
        projects=["/tmp/game"],
        verifier=fake_verifier,
        progress=lambda stage, _done: steps.append(stage),
        start=_started(plans),
    )
    assert result["state"] == "ready" and result["version"] == "0.6.0"
    assert steps.index("download") < steps.index("verify") < steps.index("unpack")
    plan = json.loads(plans[0].read_text())
    assert plan["wait_pid"] == 99 and plan["relaunch"] is False
    assert Path(plan["staged"]).name == "joinery"
    assert (Path(plan["staged"]) / "VERSION").read_text() == "new"
    assert plan["projects"] == [str(Path("/tmp/game").resolve())]
    assert updater.load_state()["pending"]["version"] == "0.6.0"
    assert not (updater.home() / "downloads/0.6.0").exists(), "the archive is not kept"
    with pytest.raises(updater.UpdateError, match="already waiting"):
        updater.install(verifier=fake_verifier, start=_started(plans))
    assert updater.restart()["state"] == "restarting"
    assert json.loads(plans[0].read_text())["relaunch"] is True


def test_bad_hash_is_never_unpacked(github: FakeGitHub) -> None:
    github.publish("0.6.0", digest="0" * 64)
    with pytest.raises(updater.UpdateError, match="SHA-256"):
        updater.install(verifier=fake_verifier, start=_started([]))
    assert not (updater.home() / "staged/0.6.0").exists()
    assert "pending" not in updater.load_state()


def test_bad_signature_is_never_unpacked(github: FakeGitHub) -> None:
    github.publish("0.6.0", bundle=b"signed:" + b"f" * 64)
    with pytest.raises(updater.UpdateError, match="signature"):
        updater.install(verifier=fake_verifier, start=_started([]))
    assert not (updater.home() / "staged/0.6.0").exists()


def test_the_real_sigstore_check_rejects_a_forged_bundle(github: FakeGitHub) -> None:
    github.publish("0.6.0", bundle=b'{"mediaType": "forged"}')
    with pytest.raises(updater.UpdateError, match="signature"):
        updater.install(
            verifier=updater.sigstore_verifier(offline=True), start=_started([])
        )


def test_unsigned_release_is_shown_but_not_installed(github: FakeGitHub) -> None:
    github.publish("0.6.0", bundle=None)
    assert updater.check(force=True)["release"]["signed"] is False
    with pytest.raises(updater.UpdateError, match="no signature"):
        updater.install(verifier=fake_verifier, start=_started([]))


def test_wrong_archive_contents_are_refused(github: FakeGitHub) -> None:
    github.publish("0.6.0", data=_archive("0.4.0"))
    with pytest.raises(updater.UpdateError, match="says it is 0.4.0"):
        updater.install(verifier=fake_verifier, start=_started([]))


def test_unsafe_archive_member_is_refused(tmp_path: Path) -> None:
    archive = tmp_path / "Joinery-0.6.0-windows-x86_64.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("../escape.txt", b"x")
    with pytest.raises(updater.UpdateError, match="Unsafe"):
        updater.extract(archive, tmp_path / "out")
    assert not (tmp_path / "escape.txt").exists()


def test_network_down_during_install(
    github: FakeGitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    github.publish("0.6.0")
    monkeypatch.setenv("JOINERY_RELEASES_URL", "http://127.0.0.1:9/releases")
    with pytest.raises(updater.Offline):
        updater.install(verifier=fake_verifier, start=_started([]))


def test_source_checkout_never_installs(
    github: FakeGitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("JOINERY_INSTALL_ROOT")
    assert updater.install_root() is None  # This test runs from a git checkout.
    github.publish("0.6.0")
    assert updater.check(force=True)["can_install"] is False
    with pytest.raises(updater.UpdateError, match="git pull"):
        updater.install(verifier=fake_verifier, start=_started([]))


def test_source_checkout_daily_check_stays_offline(
    github: FakeGitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CI and developers' editors run from a checkout: the quiet check never asks GitHub."""
    monkeypatch.delenv("JOINERY_INSTALL_ROOT")
    github.publish("0.6.0")
    quiet = updater.check(now=1000.0)
    assert github.requests == [], "no request without --force"
    assert quiet["skipped"] == "source" and not quiet["available"]
    assert not quiet["offline"] and quiet["can_install"] is False
    assert "last_check" not in updater.load_state(), "a skipped check isn't a check"
    asked = updater.check(force=True, now=1000.0)
    assert len(github.requests) == 1 and asked["release"]["version"] == "0.6.0"


def _run_helper(plan_path: Path, tmp_path: Path) -> dict[str, Any]:
    """The helper exactly as installed: a copy outside the install, isolated, no Joinery."""
    copy = tmp_path / "helper/update_swap.py"
    copy.parent.mkdir(exist_ok=True)
    copy.write_text(Path(update_swap.__file__).read_text())
    done = subprocess.run(
        [sys.executable, "-I", str(copy), str(plan_path)],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=copy.parent,
        check=True,
    )
    result: dict[str, Any] = json.loads(done.stdout)
    return result


def test_swap_after_exit_then_rollback(github: FakeGitHub, tmp_path: Path) -> None:
    github.publish("0.6.0")
    root = Path(updater.install_root() or "")
    project = tmp_path / "game"
    project.mkdir()
    (project / "project.godot").write_text("")
    plans: list[Path] = []
    updater.install(
        projects=[str(project)], verifier=fake_verifier, start=_started(plans)
    )
    plan = json.loads(plans[0].read_text())
    marker = tmp_path / "relaunched"
    plan["addon_command"] = [
        sys.executable,
        "-c",
        "import pathlib, sys; pathlib.Path(sys.argv[1], 'addon').write_text('updated')",
    ]
    plan["relaunch_argv"] = [
        sys.executable,
        "-c",
        f"open({str(marker)!r}, 'w').write('yes')",
    ]
    plan["wait_pid"] = 0
    plan["relaunch"] = True
    plans[0].write_text(json.dumps(plan))
    outcome = _run_helper(plans[0], tmp_path)
    assert outcome["ok"], outcome
    assert (root / "VERSION").read_text() == "new"
    assert (project / "addon").read_text() == "updated"
    assert outcome["addons"][0]["state"] == "updated"
    state = updater.load_state()
    assert state["previous"]["version"] == "0.5.0" and "pending" not in state
    assert (Path(state["previous"]["path"]) / "VERSION").read_text() == "old"
    for _ in range(100):
        if marker.exists():
            break
        subprocess.run(
            [sys.executable, "-c", "import time; time.sleep(0.05)"], check=True
        )
    assert marker.read_text() == "yes", "Restart now starts Joinery again"
    assert updater.check(force=True)["previous"] == "0.5.0"

    # Go back to 0.5.0: the same swap, the other way.
    updater.tag_version = lambda version="0.6.0": "0.6.0"  # type: ignore[assignment]
    plans.clear()
    updater.rollback(start=_started(plans))
    back = json.loads(plans[0].read_text())
    back.update(wait_pid=0, relaunch=False, addon_command=["true"])
    plans[0].write_text(json.dumps(back))
    assert _run_helper(plans[0], tmp_path)["ok"]
    assert (root / "VERSION").read_text() == "old"
    state = updater.load_state()
    assert state["previous"]["version"] == "0.6.0"
    assert (Path(state["previous"]["path"]) / "VERSION").read_text() == "new"


def test_failed_swap_keeps_the_old_install(tmp_path: Path) -> None:
    root = tmp_path / "joinery"
    root.mkdir()
    (root / "VERSION").write_text("old")
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "version": "0.6.0",
                "current": "0.5.0",
                "install_root": str(root),
                "staged": str(tmp_path / "missing/joinery"),
                "backup": str(tmp_path / "previous/0.5.0/joinery"),
                "home": str(tmp_path),
                "state": str(tmp_path / "state.json"),
                "projects": [],
                "relaunch": False,
            }
        )
    )
    outcome = _run_helper(plan_path, tmp_path)
    assert not outcome["ok"] and "missing" in outcome["message"]
    assert (root / "VERSION").read_text() == "old"
    assert (
        json.loads((tmp_path / "state.json").read_text())["last_install"]["ok"] is False
    )


def test_cancel_stops_the_waiting_swap(github: FakeGitHub, tmp_path: Path) -> None:
    github.publish("0.6.0")
    plans: list[Path] = []
    updater.install(verifier=fake_verifier, start=_started(plans))
    plan = json.loads(plans[0].read_text())
    assert updater.cancel()["version"] == "0.6.0"
    assert not Path(plan["staged"]).exists() and not plans[0].exists()
    assert "pending" not in updater.load_state()
    # A helper still waiting finds the plan gone and leaves the install alone.
    plan["wait_pid"] = 0
    waiting = Path(plan["home"]) / "waiting.json"
    waiting.write_text(json.dumps(plan))
    script = (
        "import json, sys, pathlib; sys.path.insert(0, sys.argv[1]); import update_swap;"
        "p = pathlib.Path(sys.argv[2]); plan = json.loads(p.read_text()); p.unlink();"
        "update_swap._read_json = lambda path: plan;"
        "print(json.dumps(update_swap.run(p)))"
    )
    done = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            script,
            str(Path(update_swap.__file__).parent),
            str(waiting),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    assert json.loads(done.stdout)["message"] == "cancelled"
    assert (Path(plan["install_root"]) / "VERSION").read_text() == "old"


def test_verify_command(
    github: FakeGitHub,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    data = _archive("0.6.0")
    archive = tmp_path / updater.asset_name("0.6.0", TARGET)
    archive.write_bytes(data)
    (tmp_path / (archive.name + ".sha256")).write_text(
        hashlib.sha256(data).hexdigest() + "  " + archive.name + "\n"
    )
    monkeypatch.setattr(updater, "sigstore_verifier", lambda **_: fake_verifier)
    args = update_cli.argparse.Namespace(archive=archive, offline=True, json=True)
    assert update_cli.verify(args) == 1
    assert "sigstore.json is missing" in capsys.readouterr().out
    (tmp_path / (archive.name + ".sigstore.json")).write_bytes(good_bundle(data))
    assert update_cli.verify(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["verified"] and result["signed_by"].endswith(
        "/.github/workflows/release.yml@refs/tags/v0.6.0"
    )
    archive.write_bytes(data + b"tampered")
    assert update_cli.verify(args) == 1
    assert "SHA-256" in capsys.readouterr().out


def test_update_command_offline_is_not_an_error(
    github: FakeGitHub,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from joinery.cli import main

    monkeypatch.setenv("JOINERY_RELEASES_URL", "http://127.0.0.1:9/releases")
    assert main(["update", "--check", "--force", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["offline"] is True
    assert main(["update", "--prereleases", "on", "--json"]) == 0
    assert updater.load_state()["prereleases"] is True
