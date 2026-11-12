"""Release signing: Sigstore on every tag, platform signing only when its secrets exist."""

from __future__ import annotations

import hashlib
import re
import zipfile
from pathlib import Path

import pytest

from joinery.catalog import ROOT
from release import platform_sign

RELEASE = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")


def _job(name: str) -> str:
    match = re.search(
        rf"\n  {re.escape(name)}:\n(.*?)(?=\n  [\w-]+:\n|\Z)", RELEASE, re.DOTALL
    )
    assert match, name
    return match.group(1)


def test_triggers_are_unchanged_so_pushes_stay_cheap() -> None:
    """Signing adds no job to ordinary pushes: release.yml runs for tags and release/ branches."""
    head = RELEASE.split("\npermissions:", 1)[0]
    assert "branches: ['release/**', '!release/*-validation']" in head
    assert "pull_request" not in head and "main" not in head


@pytest.mark.parametrize("job", ["signing", "publish"])
def test_signing_runs_only_for_a_tag(job: str) -> None:
    assert "github.event_name == 'push' && github.ref_type == 'tag'" in _job(job)


def test_platform_signing_waits_for_its_accounts() -> None:
    assert "if: needs.signing.outputs.macos == 'true'" in _job("macos-sign")
    assert "if: needs.signing.outputs.windows != 'none'" in _job("windows-sign")
    # A skipped platform job still publishes; a failed one never does.
    publish = _job("publish")
    assert "needs.macos-sign.result != 'failure'" in publish
    assert "needs.windows-sign.result != 'failure'" in publish


def test_every_archive_is_signed_and_checked_by_the_updater_code() -> None:
    publish = _job("publish")
    assert "id-token: write" in publish and "attestations: write" in publish
    assert "uv run --frozen sigstore sign out/release/Joinery-*.zip" in publish
    assert 'uv run --frozen joinery verify "$archive"' in publish
    assert "-name '*.sigstore.json' | wc -l)\" -eq 3" in publish
    assert "actions/attest-build-provenance@" in publish
    # Signing happens after the digests are checked and before publishing.
    assert publish.index("sha256sum --check") < publish.index("sigstore sign")
    assert publish.index("sigstore sign") < publish.index("gh release create")


def test_secrets_never_reach_a_command_line() -> None:
    for line in RELEASE.splitlines():
        if "secrets." in line:
            assert re.match(
                r"\s+[A-Za-z0-9_-]+: \$\{\{ secrets\.[A-Z0-9_]+ \}\}$", line
            ), line


def test_accounts_need_every_secret() -> None:
    mac = {name: "x" for name in platform_sign.MACOS_SECRETS}
    assert platform_sign.accounts({}) == {"macos": "false", "windows": "none"}
    assert platform_sign.accounts(mac)["macos"] == "true"
    assert platform_sign.accounts({**mac, "APPLE_ID": " "})["macos"] == "false"
    pfx = {name: "x" for name in platform_sign.SIGNTOOL_SECRETS}
    azure = {name: "x" for name in platform_sign.AZURE_SECRETS}
    assert platform_sign.accounts(pfx)["windows"] == "signtool"
    assert platform_sign.accounts({**pfx, **azure})["windows"] == "azure"


def test_release_notes_say_what_is_signed() -> None:
    unsigned = platform_sign.notes("false", "none")
    assert "ad-hoc signed only, not notarized" in unsigned
    assert "not Authenticode signed" in unsigned
    assert "joinery verify" in unsigned
    signed = platform_sign.notes("true", "azure")
    assert "notarized by Apple" in signed and "Authenticode signed." in signed


def test_repack_keeps_modes_and_order(tmp_path: Path) -> None:
    archive = tmp_path / "Joinery-0.6.0-windows-x86_64.zip"
    with zipfile.ZipFile(archive, "w") as package:
        for name, mode in [
            ("joinery/bin/godot.exe", 0o755),
            ("joinery/README.md", 0o644),
        ]:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = (0o100000 | mode) << 16
            package.writestr(info, b"unsigned")
    signed = tmp_path / "unpacked"
    (signed / "joinery/bin").mkdir(parents=True)
    (signed / "joinery/bin/godot.exe").write_bytes(b"signed")
    (signed / "joinery/README.md").write_bytes(b"unsigned")
    platform_sign.repack(archive, signed)
    digest = platform_sign.write_digest(archive)
    with zipfile.ZipFile(archive) as package:
        assert [i.filename for i in package.infolist()] == [
            "joinery/bin/godot.exe",
            "joinery/README.md",
        ]
        assert package.read("joinery/bin/godot.exe") == b"signed"
        assert (
            package.getinfo("joinery/bin/godot.exe").external_attr >> 16 & 0o777
            == 0o755
        )
    assert digest == hashlib.sha256(archive.read_bytes()).hexdigest()
    assert (
        tmp_path / (archive.name + ".sha256")
    ).read_text() == f"{digest}  {archive.name}\n"


def test_entitlements_match_what_godot_needs() -> None:
    text = (ROOT / "release/macos-entitlements.plist").read_text(encoding="utf-8")
    for key in (
        "allow-jit",
        "disable-library-validation",
        "allow-unsigned-executable-memory",
    ):
        assert f"com.apple.security.cs.{key}" in text
    assert "camera" not in text.split("-->", 1)[1]
