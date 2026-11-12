"""Updates from Joinery's GitHub Releases: check, verify, install side by side, go back.

    joinery update --check            # at most once a day unless --force; offline is not an error
    joinery update --install          # download, verify digest + signature, stage, swap on exit
    joinery update --restart          # the staged swap starts Joinery again when it is done
    joinery update --rollback         # go back to the version kept from the last update
    joinery update --prereleases on   # also offer alpha, beta and rc releases
    joinery verify <archive>          # check a downloaded archive by hand

Nothing is installed without the user choosing it. A release is installed only
when its archive matches the published .sha256 *and* its Sigstore bundle
(``<archive>.sigstore.json``) proves it was built by this repository's
release workflow for that exact tag (ADR 0083). A release without a bundle is
shown, but can only be downloaded by hand.

The install never overwrites the running copy: the release is unpacked into
the updates folder, and joinery/update_swap.py, copied out of the install,
waits for Joinery to exit, moves the old copy aside (kept for "Go back to"),
moves the new one into its place, and updates each open project's addon with
the new install's ``joinery update-addon``.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from joinery import __version__
from joinery.catalog import ROOT
from joinery.transactions import atomic_json

REPOSITORY = "irchiinnuss/joinery"
API = "https://api.github.com/repos/" + REPOSITORY + "/releases"
#: Who may have signed a release: GitHub Actions, running this workflow, for that tag.
ISSUER = "https://token.actions.githubusercontent.com"
WORKFLOW = ".github/workflows/release.yml"
CHECK_EVERY = 24 * 3600
TIMEOUT = 15
CHUNK = 1 << 20

#: Pre-release stages, oldest first, in tag form (v0.6.0-alpha.1) and package form (0.6.0a1).
_STAGES = {"alpha": 0, "a": 0, "beta": 1, "b": 1, "rc": 2}
_VERSION = re.compile(
    r"v?(\d+)\.(\d+)\.(\d+)(?:-(alpha|beta|rc)\.(\d+)|(a|b|rc)(\d+))?"
)
_ASSET = re.compile(r"Joinery-(.+)-((?:macos|linux|windows)-[a-z0-9_]+)\.(zip|tar\.gz)")


class UpdateError(ValueError):
    """Something the user can act on; the message says what."""


class Offline(UpdateError):
    """No network, or GitHub did not answer: never an error on screen."""


# --- versions -------------------------------------------------------------------------


def version_key(text: str) -> tuple[int, int, int, int, int] | None:
    match = _VERSION.fullmatch(text.strip())
    if match is None:
        return None
    major, minor, patch, stage, number, short, short_number = match.groups()
    stage = stage or short
    number = number or short_number
    rank = 3 if stage is None else _STAGES[stage]
    return int(major), int(minor), int(patch), rank, int(number or 0)


def tag_version(version: str = __version__) -> str:
    """0.6.0a1 -> 0.6.0-alpha.1, the form used in tags and file names."""
    match = _VERSION.fullmatch(version)
    if match is None:
        return version
    major, minor, patch, stage, number, short, short_number = match.groups()
    if stage:
        return f"{major}.{minor}.{patch}-{stage}.{number}"
    if short:
        name = {"a": "alpha", "b": "beta", "rc": "rc"}[short]
        return f"{major}.{minor}.{patch}-{name}.{short_number}"
    return f"{major}.{minor}.{patch}"


def package_version(version: str) -> str:
    """0.6.0-alpha.1 -> 0.6.0a1, the form a release's manifest.json records."""
    match = _VERSION.fullmatch(version.lstrip("v"))
    if match is None:
        return version
    major, minor, patch, stage, number, short, short_number = match.groups()
    stage, number = stage or short, number or short_number
    if stage is None:
        return f"{major}.{minor}.{patch}"
    return f"{major}.{minor}.{patch}{ {'alpha': 'a', 'beta': 'b'}.get(stage, stage) }{number}"


# --- where things live -----------------------------------------------------------------


def platform_target() -> str | None:
    machine = platform.machine().lower()
    arch = {
        "arm64": "arm64",
        "aarch64": "arm64",
        "x86_64": "x86_64",
        "amd64": "x86_64",
    }.get(machine)
    system = {"darwin": "macos", "linux": "linux", "win32": "windows"}.get(sys.platform)
    target = f"{system}-{arch}" if system and arch else None
    # The targets Joinery publishes; anything else has nothing to download.
    return (
        target if target in {"macos-arm64", "linux-x86_64", "windows-x86_64"} else None
    )


def asset_name(version: str, target: str) -> str:
    return f"Joinery-{version}-{target}." + (
        "tar.gz" if target.startswith("linux-") else "zip"
    )


def home() -> Path:
    """The updates folder: state, downloads, staged copies and the kept older version."""
    override = os.environ.get("JOINERY_UPDATE_HOME")
    if override:
        return Path(override)
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/Joinery/updates"
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData/Local")
        return Path(base) / "Joinery/updates"
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local/state")
    return Path(base) / "joinery/updates"


def install_root() -> Path | None:
    """The folder the update replaces, or None when running from a source checkout."""
    override = os.environ.get("JOINERY_INSTALL_ROOT")
    if override:
        return Path(override)
    if (ROOT / ".git").exists():
        return None
    if ROOT.parent.name == "Resources" and ROOT.parent.parent.name == "Contents":
        app = ROOT.parent.parent.parent
        return app if app.suffix == ".app" else None
    return ROOT if (ROOT / "manifest.json").is_file() else None


def runtime_of(root: Path) -> Path:
    """The Joinery source folder inside an install (where pyproject.toml is)."""
    return root / "Contents/Resources/joinery" if root.suffix == ".app" else root


def _state_path() -> Path:
    return home() / "state.json"


def load_state() -> dict[str, Any]:
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_state(state: dict[str, Any]) -> None:
    atomic_json(_state_path(), state)


def set_prereleases(on: bool) -> dict[str, Any]:
    state = load_state()
    state["prereleases"] = on
    state.pop("last_check", None)  # The next check looks again with the new choice.
    save_state(state)
    return {"prereleases": on}


def dismiss(version: str) -> dict[str, Any]:
    """Not now: the daily check stops offering this version (a manual check still shows it)."""
    state = load_state()
    state["dismissed"] = version
    save_state(state)
    return {"dismissed": version}


# --- talking to GitHub -----------------------------------------------------------------


def _releases_url() -> str:
    return os.environ.get("JOINERY_RELEASES_URL") or API


def _allowed(url: str) -> bool:
    """Only HTTPS from GitHub; plain local URLs only when tests point the updater at one."""
    parts = urllib.parse.urlsplit(url)
    if os.environ.get("JOINERY_RELEASES_URL"):
        return parts.scheme in {"http", "https"}
    return parts.scheme == "https"


def _open(url: str, accept: str = "application/octet-stream") -> Any:
    if not _allowed(url):
        raise UpdateError("Refusing to download from " + url)
    request = urllib.request.Request(
        url, headers={"Accept": accept, "User-Agent": "Joinery/" + __version__}
    )
    try:
        return urllib.request.urlopen(request, timeout=TIMEOUT)
    except urllib.error.HTTPError as error:
        if error.code in {403, 429}:
            raise Offline(
                "GitHub asked Joinery to wait; it will check again later."
            ) from None
        raise UpdateError(f"GitHub answered {error.code} for {url}.") from None
    except (urllib.error.URLError, OSError, TimeoutError) as error:
        raise Offline("Joinery is offline; it will check again later.") from error


def _read(
    url: str, accept: str = "application/octet-stream", limit: int = 4 << 20
) -> bytes:
    with _open(url, accept) as response:
        data: bytes = response.read(limit + 1)
    if len(data) > limit:
        raise UpdateError("The answer from " + url + " is unexpectedly large.")
    return data


def releases() -> list[dict[str, Any]]:
    url = _releases_url() + ("&" if "?" in _releases_url() else "?") + "per_page=30"
    data = json.loads(_read(url, "application/vnd.github+json"))
    if not isinstance(data, list):
        raise UpdateError("GitHub's release list was not a list.")
    return [item for item in data if isinstance(item, dict)]


@dataclass(frozen=True)
class Release:
    version: str
    prerelease: bool
    notes: str
    url: str
    asset: dict[str, Any]
    digest_url: str
    bundle_url: str

    def public(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "prerelease": self.prerelease,
            "notes": self.notes,
            "url": self.url,
            "asset": self.asset["name"],
            "size": int(self.asset.get("size") or 0),
            "signed": bool(self.bundle_url),
        }


def pick(
    items: list[dict[str, Any]], *, current: str, target: str, prereleases: bool
) -> Release | None:
    """The newest release newer than ``current`` that has an archive for ``target``."""
    now = version_key(current)
    best: tuple[tuple[int, int, int, int, int], Release] | None = None
    for item in items:
        if item.get("draft") or (item.get("prerelease") and not prereleases):
            continue
        version = str(item.get("tag_name", "")).lstrip("v")
        key = version_key(version)
        if key is None or now is None or key <= now:
            continue
        if key[3] < 3 and not prereleases:
            continue
        assets = {
            str(a.get("name")): a for a in item.get("assets", []) if isinstance(a, dict)
        }
        name = asset_name(version, target)
        if name not in assets or name + ".sha256" not in assets:
            continue
        bundle = assets.get(name + ".sigstore.json", {})
        release = Release(
            version=version,
            prerelease=bool(item.get("prerelease")) or key[3] < 3,
            notes=str(item.get("body") or ""),
            url=str(item.get("html_url") or ""),
            asset=assets[name],
            digest_url=str(assets[name + ".sha256"].get("browser_download_url", "")),
            bundle_url=str(bundle.get("browser_download_url", "")),
        )
        if best is None or key > best[0]:
            best = (key, release)
    return best[1] if best else None


def _summary(state: dict[str, Any]) -> dict[str, Any]:
    root = install_root()
    previous = state.get("previous") or {}
    return {
        "current": tag_version(),
        "prereleases": bool(state.get("prereleases")),
        "can_install": root is not None and platform_target() is not None,
        "previous": previous.get("version", "")
        if previous and Path(str(previous.get("path", ""))).exists()
        else "",
        "pending": (state.get("pending") or {}).get("version", ""),
        "last_install": state.get("last_install") or {},
    }


def check(*, force: bool = False, now: float | None = None) -> dict[str, Any]:
    """What is available. Cached for a day unless forced; offline answers, never raises."""
    now = time.time() if now is None else now
    state = load_state()
    summary = _summary(state)
    cached = state.get("check") if isinstance(state.get("check"), dict) else None
    if (
        not force
        and cached is not None
        and cached.get("current") == summary["current"]
        and now - float(state.get("last_check", 0)) < CHECK_EVERY
    ):
        return {
            **cached,
            **summary,
            "cached": True,
            "dismissed": state.get("dismissed", ""),
        }
    if not force and not summary["can_install"]:
        # A source checkout (CI, a developer's editor) never asks GitHub on its own; only an
        # explicit check (Help ▸ Check for Joinery updates…, --force) does.
        return {
            **summary,
            "available": False,
            "offline": False,
            "skipped": "source",
            "cached": False,
            "dismissed": state.get("dismissed", ""),
        }
    target = platform_target()
    result: dict[str, Any] = {**summary, "available": False, "offline": False}
    try:
        release = (
            pick(
                releases(),
                current=summary["current"],
                target=target,
                prereleases=summary["prereleases"],
            )
            if target
            else None
        )
    except Offline as error:
        return {**result, "offline": True, "message": str(error)}
    except (UpdateError, ValueError) as error:
        return {**result, "message": str(error)}
    if release is not None:
        result.update(available=True, release=release.public())
    result["checked"] = datetime.datetime.fromtimestamp(now, datetime.UTC).isoformat(
        timespec="seconds"
    )
    state["check"] = {k: v for k, v in result.items() if k not in summary}
    state["check"]["current"] = summary["current"]
    state["last_check"] = now
    save_state(state)
    return {**result, "cached": False, "dismissed": state.get("dismissed", "")}


# --- verifying -------------------------------------------------------------------------

#: verifier(digest, bundle_json, version): raises UpdateError unless the bundle is good.
Verifier = Callable[[bytes, bytes, str], None]


def signer_identity(version: str, repository: str = REPOSITORY) -> str:
    return f"https://github.com/{repository}/{WORKFLOW}@refs/tags/v{version}"


def sigstore_verifier(
    *,
    offline: bool = False,
    repository: str = REPOSITORY,
    policy_for: Callable[[str], Any] | None = None,
) -> Verifier:
    """Sigstore's own verifier (pinned in uv.lock), with Joinery's release identity.

    ``policy_for(version)`` replaces the identity policy; only for checking this
    code against another project's real bundle, never for Joinery's releases.
    """

    def verify(digest: bytes, bundle_json: bytes, version: str) -> None:
        try:
            from sigstore.errors import Error as SigstoreError
            from sigstore.hashes import Hashed
            from sigstore.models import Bundle
            from sigstore.verify import Verifier as SigstoreVerifier
            from sigstore.verify import policy
            from sigstore_models.common.v1 import HashAlgorithm
        except ImportError as error:  # pragma: no cover - the dependency is pinned
            raise UpdateError(
                "Sigstore is not installed, so the signature cannot be checked."
            ) from error
        try:
            bundle = Bundle.from_json(bundle_json)
            identity = (
                policy_for(version)
                if policy_for
                else policy.AllOf(
                    [
                        policy.Identity(
                            identity=signer_identity(version, repository),
                            issuer=ISSUER,
                        ),
                        policy.GitHubWorkflowRepository(repository),
                        policy.GitHubWorkflowRef(f"refs/tags/v{version}"),
                    ]
                )
            )
            SigstoreVerifier.production(offline=offline).verify_artifact(
                Hashed(algorithm=HashAlgorithm.SHA2_256, digest=digest),
                bundle,
                identity,
            )
        except SigstoreError as error:
            raise UpdateError(
                "The signature does not match this release: " + str(error)
            ) from None
        except (ValueError, KeyError, TypeError) as error:
            raise UpdateError(
                "The signature bundle could not be read: " + str(error)
            ) from None

    return verify


def file_digest(path: Path) -> bytes:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(CHUNK), b""):
            digest.update(chunk)
    return digest.digest()


def check_digest_file(text: str, name: str, digest: bytes) -> None:
    fields = text.strip().split()
    if len(fields) != 2 or fields[1].lstrip("*") != name:
        raise UpdateError(f"The .sha256 file does not describe {name}.")
    if fields[0].lower() != digest.hex():
        raise UpdateError(
            f"{name} does not match its published SHA-256; it was not installed."
        )


def verify(
    archive: Path,
    *,
    verifier: Verifier | None = None,
    offline: bool = False,
) -> dict[str, Any]:
    """`joinery verify`: the archive against the .sha256 and .sigstore.json beside it."""
    match = _ASSET.fullmatch(archive.name)
    if match is None:
        raise UpdateError(
            "Expected a Joinery release archive (Joinery-<version>-<platform>.zip)."
        )
    version = match.group(1)
    digest = file_digest(archive)
    sha_file = archive.with_name(archive.name + ".sha256")
    bundle_file = archive.with_name(archive.name + ".sigstore.json")
    if not sha_file.is_file():
        raise UpdateError(
            f"{sha_file.name} is missing; download it from the same release."
        )
    check_digest_file(sha_file.read_text(encoding="utf-8"), archive.name, digest)
    if not bundle_file.is_file():
        raise UpdateError(
            f"{bundle_file.name} is missing: this release is not signed, or the signature was not downloaded."
        )
    (verifier or sigstore_verifier(offline=offline))(
        digest, bundle_file.read_bytes(), version
    )
    return {
        "archive": str(archive),
        "version": version,
        "target": match.group(2),
        "sha256": digest.hex(),
        "signed_by": signer_identity(version),
        "verified": True,
    }


# --- downloading and staging -----------------------------------------------------------


def _download(
    url: str, destination: Path, size: int, progress: Callable[[float], None]
) -> bytes:
    digest = hashlib.sha256()
    done = 0
    destination.parent.mkdir(parents=True, exist_ok=True)
    with _open(url) as response, destination.open("wb") as file:
        while True:
            chunk = response.read(CHUNK)
            if not chunk:
                break
            done += len(chunk)
            if size and done > size:
                raise UpdateError(
                    "The download is larger than GitHub said it would be."
                )
            digest.update(chunk)
            file.write(chunk)
            if size:
                progress(done / size)
    if size and done != size:
        raise Offline(
            "The download stopped early; try again when the connection is steady."
        )
    return digest.digest()


def _member_ok(name: str) -> bool:
    path = Path(name)
    return (
        not path.is_absolute()
        and ".." not in path.parts
        and not name.startswith(("/", "\\"))
    )


def extract(archive: Path, destination: Path) -> None:
    """Unpack without letting any member escape or link elsewhere."""
    destination.mkdir(parents=True, exist_ok=True)
    if archive.name.endswith(".tar.gz"):
        with tarfile.open(archive) as package:
            package.extractall(destination, filter="data")
        return
    with zipfile.ZipFile(archive) as package:
        for info in package.infolist():
            if not _member_ok(info.filename) or stat.S_ISLNK(info.external_attr >> 16):
                raise UpdateError(
                    "Unsafe file in the release archive: " + info.filename
                )
        if sys.platform == "darwin" and shutil.which("ditto"):
            # ditto keeps the code signature a signed .app carries in extended attributes.
            subprocess.run(
                ["ditto", "-x", "-k", str(archive), str(destination)],
                check=True,
                capture_output=True,
                timeout=600,
            )
            return
        for info in package.infolist():
            package.extract(info, destination)
            if not info.is_dir():
                mode = info.external_attr >> 16 & 0o777
                (destination / info.filename).chmod(mode or 0o644)


def _check_layout(staged: Path, version: str, target: str) -> Path:
    name = "Joinery.app" if target.startswith("macos-") else "joinery"
    top = [p.name for p in staged.iterdir() if p.name not in {"__MACOSX", ".DS_Store"}]
    if top != [name]:
        raise UpdateError(
            f"The archive should hold one {name} folder, not {', '.join(top)}."
        )
    item = staged / name
    manifest_path = runtime_of(item) / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise UpdateError("The archive has no readable manifest.json.") from None
    if (
        manifest.get("joinery") != package_version(version)
        or manifest.get("target") != target
    ):
        raise UpdateError(
            f"The archive says it is {manifest.get('joinery')} for {manifest.get('target')}, "
            f"not {version} for {target}."
        )
    return item


def stage(
    release: Release,
    *,
    target: str,
    verifier: Verifier | None = None,
    progress: Callable[[str, float], None] = lambda _stage, _done: None,
) -> Path:
    """Download, check digest then signature, unpack. Returns the staged app folder."""
    if not release.bundle_url:
        raise UpdateError(
            f"Joinery {release.version} has no signature, so it can't be installed from here. "
            f"Download it from {release.url} instead."
        )
    base = home()
    folder = base / "downloads" / release.version
    shutil.rmtree(folder, ignore_errors=True)
    archive = folder / str(release.asset["name"])
    try:
        digest = _download(
            str(release.asset.get("browser_download_url", "")),
            archive,
            int(release.asset.get("size") or 0),
            lambda done: progress("download", done),
        )
        progress("verify", 0.0)
        check_digest_file(
            _read(release.digest_url).decode("utf-8", "replace"), archive.name, digest
        )
        (verifier or sigstore_verifier())(
            digest, _read(release.bundle_url), release.version
        )
        progress("unpack", 0.0)
        staged = base / "staged" / release.version
        shutil.rmtree(staged, ignore_errors=True)
        temporary = Path(tempfile.mkdtemp(prefix=".unpack-", dir=base))
        try:
            extract(archive, temporary)
            _check_layout(temporary, release.version, target)
            staged.parent.mkdir(parents=True, exist_ok=True)
            os.replace(temporary, staged)
        finally:
            shutil.rmtree(temporary, ignore_errors=True)
        return _check_layout(staged, release.version, target)
    finally:
        shutil.rmtree(folder, ignore_errors=True)


# --- swapping on exit ------------------------------------------------------------------


def open_projects(root: Path) -> list[str]:
    """Projects open in editors started from this install (best effort)."""
    prefix = str(root.resolve())
    lines: list[str] = []
    try:
        if os.name == "nt":
            from joinery.platform_support import _windows_editor_query

            powershell = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
            if powershell:
                rows = json.loads(_windows_editor_query(powershell).stdout or "[]")
                rows = [rows] if isinstance(rows, dict) else rows
                lines = [
                    str(row.get("CommandLine") or "")
                    for row in rows
                    if str(row.get("ExecutablePath") or "")
                    .lower()
                    .startswith(prefix.lower())
                ]
        else:
            result = subprocess.run(
                ["ps", "-A", "-o", "args="],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            lines = [
                line
                for line in result.stdout.splitlines()
                if line.strip().startswith(prefix)
            ]
    except (OSError, ValueError, subprocess.SubprocessError):
        return []
    found: list[str] = []
    for line in lines:
        words = line.replace('"', " ").split()
        for index, word in enumerate(words[:-1]):
            if word != "--path":
                continue
            # ps drops quoting; grow the path a word at a time until it is a project.
            for end in range(index + 2, len(words) + 1):
                candidate = Path(" ".join(words[index + 1 : end]))
                if (candidate / "project.godot").is_file():
                    if str(candidate) not in found:
                        found.append(str(candidate))
                    break
    return found


def relaunch_argv(root: Path, project: str = "") -> list[str]:
    extra = ["--editor", "--path", project] if project else []
    if root.suffix == ".app":
        return ["open", "-n", str(root), *(["--args", *extra] if extra else [])]
    if os.name == "nt":
        return ["cmd.exe", "/c", "start", "", str(root / "Joinery.cmd"), *extra]
    return [str(root / "Joinery"), *extra]


def _helper_python() -> str:
    return sys.executable


def _start_helper(plan_path: Path) -> int:
    helper = home() / "update_swap.py"
    shutil.copy2(Path(__file__).with_name("update_swap.py"), helper)
    flags = 0
    if os.name == "nt":
        flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    child = subprocess.Popen(
        [_helper_python(), "-I", str(helper), str(plan_path)],
        cwd=home(),  # never the install: Windows would keep it locked
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
        start_new_session=os.name != "nt",
    )
    return child.pid


def _plan(
    *,
    kind: str,
    version: str,
    staged: Path,
    root: Path,
    wait_pid: int,
    projects: list[str],
) -> dict[str, Any]:
    current = tag_version()
    first = projects[0] if projects else ""
    return {
        "kind": kind,
        "version": version,
        "current": current,
        "install_root": str(root),
        "staged": str(staged),
        "backup": str(home() / "previous" / current / root.name),
        "runtime": str(runtime_of(root)),
        "home": str(home()),
        "state": str(_state_path()),
        "wait_pid": wait_pid,
        "projects": projects,
        "relaunch": False,
        "relaunch_argv": relaunch_argv(root, first),
        "fallback_argv": relaunch_argv(root, first),
    }


def _schedule(
    plan: dict[str, Any], *, start: Callable[[Path], int] | None = None
) -> dict[str, Any]:
    plan_path = home() / "plan.json"
    atomic_json(plan_path, plan)
    state = load_state()
    state["pending"] = {
        "version": plan["version"],
        "kind": plan["kind"],
        "plan": str(plan_path),
    }
    save_state(state)
    helper = (start or _start_helper)(plan_path)
    return {
        "state": "ready",
        "kind": plan["kind"],
        "version": plan["version"],
        "helper": helper,
        "projects": plan["projects"],
        "message": f"Joinery {plan['version']} is ready. Restart Joinery to finish.",
    }


def _require_root() -> Path:
    root = install_root()
    if root is None:
        raise UpdateError(
            "This Joinery runs from a source checkout; update it with git pull instead."
        )
    if load_state().get("pending"):
        plan = home() / "plan.json"
        if plan.is_file():
            raise UpdateError("An update is already waiting for Joinery to restart.")
    return root


def install(
    *,
    wait_pid: int = 0,
    projects: list[str] | None = None,
    verifier: Verifier | None = None,
    progress: Callable[[str, float], None] = lambda _stage, _done: None,
    start: Callable[[Path], int] | None = None,
) -> dict[str, Any]:
    root = _require_root()
    target = platform_target()
    if target is None:
        raise UpdateError("Joinery doesn't publish a build for this computer.")
    state = load_state()
    try:
        release = pick(
            releases(),
            current=tag_version(),
            target=target,
            prereleases=bool(state.get("prereleases")),
        )
    except Offline as error:
        raise Offline("You're offline. Connect, then try the update again.") from error
    if release is None:
        return {"state": "current", "version": tag_version()}
    staged = stage(release, target=target, verifier=verifier, progress=progress)
    open_now = [*(projects or []), *open_projects(root)]
    return _schedule(
        _plan(
            kind="install",
            version=release.version,
            staged=staged,
            root=root,
            wait_pid=wait_pid,
            projects=list(dict.fromkeys(str(Path(p).resolve()) for p in open_now)),
        ),
        start=start,
    )


def rollback(
    *,
    wait_pid: int = 0,
    projects: list[str] | None = None,
    start: Callable[[Path], int] | None = None,
) -> dict[str, Any]:
    root = _require_root()
    previous = load_state().get("previous") or {}
    path = Path(str(previous.get("path", "")))
    if not previous or not path.exists():
        raise UpdateError("There is no earlier version to go back to.")
    open_now = [*(projects or []), *open_projects(root)]
    return _schedule(
        _plan(
            kind="rollback",
            version=str(previous["version"]),
            staged=path,
            root=root,
            wait_pid=wait_pid,
            projects=list(dict.fromkeys(str(Path(p).resolve()) for p in open_now)),
        ),
        start=start,
    )


def restart() -> dict[str, Any]:
    """Restart now: the waiting swap starts Joinery again once it is done."""
    plan_path = home() / "plan.json"
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise UpdateError("No update is waiting.") from None
    plan["relaunch"] = True
    atomic_json(plan_path, plan)
    return {"state": "restarting", "version": plan.get("version", "")}


def cancel() -> dict[str, Any]:
    """Drop a staged update that has not been swapped in yet."""
    plan_path = home() / "plan.json"
    state = load_state()
    pending = state.pop("pending", None)
    save_state(state)
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        plan_path.unlink()
    except (OSError, ValueError):
        plan = {}
    if plan.get("kind") == "install" and plan.get("staged"):
        shutil.rmtree(Path(plan["staged"]).parent, ignore_errors=True)
    return {"state": "cancelled", "version": (pending or {}).get("version", "")}
