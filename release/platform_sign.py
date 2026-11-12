"""Platform code signing for release archives; on only when the accounts exist.

    python release/platform_sign.py accounts            # which signing secrets are set (no values)
    python release/platform_sign.py macos ARCHIVE       # Developer ID + notarize + staple
    python release/platform_sign.py windows ARCHIVE --signed-by DIR   # after signtool/Azure
    python release/platform_sign.py digest ARCHIVE      # rewrite ARCHIVE.sha256

release.yml runs this only for a tag (docs/signing.md, ADR 0083). Without the
secrets the archives stay as native.yml built them: ad-hoc signed on macOS,
unsigned on Windows. Secrets come from the environment and are never printed.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import tempfile
import zipfile
from collections.abc import Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

MACOS_SECRETS = (
    "APPLE_CERT_P12",
    "APPLE_CERT_PASSWORD",
    "APPLE_ID",
    "APPLE_TEAM_ID",
    "APPLE_APP_PASSWORD",
)
AZURE_SECRETS = (
    "AZURE_TENANT_ID",
    "AZURE_CLIENT_ID",
    "AZURE_CLIENT_SECRET",
    "AZURE_SIGNING_ENDPOINT",
    "AZURE_SIGNING_ACCOUNT",
    "AZURE_CERT_PROFILE",
)
SIGNTOOL_SECRETS = ("WINDOWS_CERT_PFX", "WINDOWS_CERT_PASSWORD")


def accounts(env: Mapping[str, str]) -> dict[str, str]:
    """Which signing each platform gets, from which secrets are non-empty."""

    def present(names: tuple[str, ...]) -> bool:
        return all(env.get(name, "").strip() for name in names)

    windows = (
        "azure"
        if present(AZURE_SECRETS)
        else "signtool"
        if present(SIGNTOOL_SECRETS)
        else "none"
    )
    return {"macos": "true" if present(MACOS_SECRETS) else "false", "windows": windows}


def notes(macos: str, windows: str) -> str:
    """The signing paragraph appended to the release notes."""
    lines = [
        "",
        "## Signatures",
        "",
        "Every archive has a Sigstore signature (`<archive>.sigstore.json`) made by this",
        "repository's release workflow for this tag, and a GitHub build attestation. Joinery's",
        "updater checks both the SHA-256 and the signature before installing; by hand:",
        "`joinery verify <archive>` or `gh attestation verify <archive> --repo irchiinnuss/joinery`.",
        "",
    ]
    lines.append(
        "- macOS: signed with a Developer ID and notarized by Apple."
        if macos == "true"
        else "- macOS: ad-hoc signed only, not notarized. The first time, open Joinery with"
        " right-click → Open (or System Settings → Privacy & Security → Open Anyway)."
    )
    lines.append(
        "- Windows: Authenticode signed."
        if windows != "none"
        else "- Windows: not Authenticode signed. SmartScreen may warn; choose More info → Run anyway."
    )
    lines.append("- Linux: no platform signing; use the Sigstore signature.")
    return "\n".join(lines) + "\n"


def write_digest(archive: Path) -> str:
    digest = hashlib.sha256()
    with archive.open("rb") as file:
        for chunk in iter(lambda: file.read(1 << 20), b""):
            digest.update(chunk)
    text = f"{digest.hexdigest()}  {archive.name}\n"
    archive.with_name(archive.name + ".sha256").write_text(text, encoding="utf-8")
    return digest.hexdigest()


def repack(archive: Path, folder: Path) -> None:
    """Rewrite a zip from an extracted folder, keeping each entry's name, mode and date.

    Windows' own zip tools drop the Unix modes the archive carries, so the signed
    files are put back into the original entry list instead.
    """
    temporary = archive.with_name(archive.name + ".repack")
    with (
        zipfile.ZipFile(archive) as source,
        zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as output,
    ):
        for info in source.infolist():
            if info.is_dir():
                output.writestr(info, b"")
                continue
            output.writestr(info, (folder / info.filename).read_bytes())
    os.replace(temporary, archive)


def _run(argv: list[str], **kwargs: object) -> None:
    subprocess.run(argv, check=True, **kwargs)  # type: ignore[call-overload]


def sign_macos(archive: Path, *, identity: str, notarize: bool) -> None:
    """codesign (hardened runtime, timestamp) → notarytool → stapler → ditto zip."""
    work = Path(tempfile.mkdtemp(prefix="joinery-sign-"))
    try:
        _run(["ditto", "-x", "-k", str(archive), str(work)])
        app = work / "Joinery.app"
        entitlements = ROOT / "release/macos-entitlements.plist"
        common = ["codesign", "--force", "--sign", identity]
        if identity != "-":
            common += ["--timestamp", "--options", "runtime"]
        # Inside out: the engine binary first, then the bundle seals the rest.
        _run(
            [
                *common,
                "--entitlements",
                str(entitlements),
                str(app / "Contents/MacOS/Godot"),
            ]
        )
        _run([*common, "--entitlements", str(entitlements), str(app)])
        _run(["codesign", "--verify", "--deep", "--strict", "--verbose=2", str(app)])
        if notarize:
            upload = work / "notarize.zip"
            _run(["ditto", "-c", "-k", "--keepParent", str(app), str(upload)])
            _run(
                [
                    "xcrun", "notarytool", "submit", str(upload),
                    "--apple-id", os.environ["APPLE_ID"],
                    "--team-id", os.environ["APPLE_TEAM_ID"],
                    "--password", os.environ["APPLE_APP_PASSWORD"],
                    "--wait", "--timeout", "45m",
                ]
            )  # fmt: skip
            upload.unlink()
            _run(["xcrun", "stapler", "staple", str(app)])
            _run(["spctl", "--assess", "--type", "execute", "--verbose=2", str(app)])
        signed = archive.with_name(archive.name + ".signed")
        _run(["ditto", "-c", "-k", "--keepParent", str(app), str(signed)])
        os.replace(signed, archive)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    write_digest(archive)


def import_certificate(keychain_password: str) -> str:
    """Put the Developer ID certificate in a throwaway keychain; return its identity."""
    import base64

    work = Path(os.environ.get("RUNNER_TEMP", tempfile.gettempdir()))
    certificate = work / "developer-id.p12"
    keychain = work / "joinery-signing.keychain-db"
    certificate.write_bytes(base64.b64decode(os.environ["APPLE_CERT_P12"]))
    try:
        _run(["security", "create-keychain", "-p", keychain_password, str(keychain)])
        _run(["security", "set-keychain-settings", "-lut", "21600", str(keychain)])
        _run(["security", "unlock-keychain", "-p", keychain_password, str(keychain)])
        _run(
            [
                "security", "import", str(certificate), "-k", str(keychain),
                "-P", os.environ["APPLE_CERT_PASSWORD"], "-T", "/usr/bin/codesign",
            ]
        )  # fmt: skip
        _run(
            [
                "security", "set-key-partition-list", "-S", "apple-tool:,apple:",
                "-s", "-k", keychain_password, str(keychain),
            ],
            stdout=subprocess.DEVNULL,
        )  # fmt: skip
        _run(["security", "list-keychains", "-d", "user", "-s", str(keychain)])
    finally:
        certificate.unlink(missing_ok=True)
    found = subprocess.run(
        ["security", "find-identity", "-v", "-p", "codesigning", str(keychain)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    for line in found.splitlines():
        if "Developer ID Application" in line:
            return line.split('"')[1]
    raise SystemExit("The certificate is not a Developer ID Application certificate.")


def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("accounts")
    macos = commands.add_parser("macos")
    macos.add_argument("archive", type=Path)
    macos.add_argument("--identity", help="'-' for an ad-hoc local rehearsal")
    macos.add_argument("--no-notarize", action="store_true")
    windows = commands.add_parser("windows")
    windows.add_argument("archive", type=Path)
    windows.add_argument("--signed-by", type=Path, required=True)
    digest = commands.add_parser("digest")
    digest.add_argument("archive", type=Path)
    paragraph = commands.add_parser("notes")
    paragraph.add_argument("--macos", default="false")
    paragraph.add_argument("--windows", default="none")
    args = parser.parse_args()
    if args.command == "notes":
        print(notes(args.macos, args.windows), end="")
        return
    if args.command == "accounts":
        found = accounts(os.environ)
        lines = "".join(f"{key}={value}\n" for key, value in found.items())
        if "GITHUB_OUTPUT" in os.environ:
            with Path(os.environ["GITHUB_OUTPUT"]).open(
                "a", encoding="utf-8"
            ) as output:
                output.write(lines)
        print(lines, end="")
    elif args.command == "macos":
        identity = args.identity or import_certificate(os.urandom(24).hex())
        sign_macos(args.archive, identity=identity, notarize=not args.no_notarize)
    elif args.command == "windows":
        repack(args.archive, args.signed_by)
        write_digest(args.archive)
    else:
        write_digest(args.archive)


if __name__ == "__main__":
    main()
