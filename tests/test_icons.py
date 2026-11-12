"""The Joinery icon outputs are the ones the recorded recipe makes."""

import hashlib
import importlib.util
import json
import struct
from pathlib import Path
from types import ModuleType

ICON = Path(__file__).resolve().parents[1] / "release/icon"
LOCK = json.loads((ICON / "icons.lock.json").read_text())


def _builder() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "build_icons", ICON / "build_icons.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sources_are_the_recorded_hand_written_svgs() -> None:
    assert LOCK["origin"] == "tool-created"
    for name, digest in LOCK["recipe"]["sources"].items():
        text = (ICON / name).read_text()
        assert text.startswith("<svg") and "<image" not in text
        assert hashlib.sha256(text.encode()).hexdigest() == digest


def test_containers_match_the_lock() -> None:
    # PNGs live in Git LFS and may be pointers in a light checkout; the
    # containers and the desktop entry are plain files everywhere.
    for name in ("dist/joinery.ico", "dist/joinery.icns", "dist/joinery.desktop"):
        data = (ICON / name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == LOCK["outputs"][name], name


def test_ico_and_icns_layouts() -> None:
    ico = (ICON / "dist/joinery.ico").read_bytes()
    reserved, kind, count = struct.unpack_from("<HHH", ico)
    assert (reserved, kind) == (0, 1)
    sides = [ico[6 + 16 * i] or 256 for i in range(count)]
    assert sides == list(_builder().ICO_SIZES)
    icns = (ICON / "dist/joinery.icns").read_bytes()
    assert icns[:4] == b"icns" and struct.unpack_from(">I", icns, 4)[0] == len(icns)
    offset, kinds = 8, []
    while offset < len(icns):
        kinds.append(icns[offset : offset + 4].decode())
        assert icns[offset + 8 : offset + 12] == b"\x89PNG"
        offset += struct.unpack_from(">I", icns, offset + 4)[0]
    assert kinds == [kind for kind, _ in _builder().ICNS_SLOTS]
