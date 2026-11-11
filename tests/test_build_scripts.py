"""Exercise checksum recording with actual bytes and no network dependency."""

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from joinery.catalog import ROOT


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX sandbox build wrapper; native Windows builds use MSVC",
)
def test_checksum_fetch_keeps_every_downloaded_hash_and_existing_entry(
    tmp_path: Path,
) -> None:
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    for name in ("fetch-checksums.sh", "versions.env"):
        shutil.copy2(ROOT / "sandbox" / name, sandbox / name)
    (sandbox / "checksums.txt").write_text("# fixture\nretained " + "a" * 64 + "\n")
    commands = tmp_path / "bin"
    commands.mkdir()
    curl = commands / "curl"
    curl.write_text(
        "#!"
        + sys.executable
        + '\nimport sys\nfrom pathlib import Path\nargs=sys.argv[1:]\nPath(args[args.index("-o")+1]).write_bytes(b"real fixture bytes")\n'
    )
    curl.chmod(0o755)
    subprocess.run(
        ["bash", str(sandbox / "fetch-checksums.sh")],
        env=dict(os.environ, PATH=str(commands) + os.pathsep + os.environ["PATH"]),
        check=True,
        capture_output=True,
    )
    rows = (sandbox / "checksums.txt").read_text().splitlines()
    entries = dict(line.split() for line in rows if line and not line.startswith("#"))
    expected = hashlib.sha256(b"real fixture bytes").hexdigest()
    assert set(entries) == {
            "godot",
            "blender",
            "mpfb2",
            "materialmaker",
            "surge",
            "godotmcp",
            "retained",
        }
    assert all(
        entries[name] == expected
        for name in ("godot", "blender", "mpfb2", "materialmaker", "godotmcp")
    )
    assert entries["retained"] == "a" * 64
