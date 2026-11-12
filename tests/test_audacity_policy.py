"""The Audacity worker never has a speech model, and its transcription tools are refused."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from joinery.catalog import blocked_tools, load_catalog
from joinery.hook import mcp_violation
from joinery.mcp_runtime import MCPConnection, ServerSpec
from joinery.policy import Severity

ROOT = Path(__file__).resolve().parents[1]
SANDBOX = ROOT / "sandbox"

#: Every speech-to-text tool of the pinned Audacity MCP (audacity_mcp/tools/
#: transcription_tools.py at the catalog's ref).
TRANSCRIPTION_TOOLS = (
    "transcribe_audio",
    "transcribe_selection",
    "transcribe_to_file",
    "transcribe_to_labels",
    "transcription_set_model",
    "check_transcription_status",
    "get_default_transcription_folder",
)
#: Speech and model runtimes the Audacity MCP's own manifest pulls in
#: (faster-whisper and what it depends on), and their close relatives.
MODEL_PACKAGES = frozenset(
    {
        "faster-whisper",
        "openai-whisper",
        "whisper",
        "ctranslate2",
        "onnxruntime",
        "torch",
        "transformers",
        "huggingface-hub",
        "tokenizers",
        "av",
    }
)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "joinery.toml").write_text('[tools]\nenabled=["godot","audacity"]\n')
    return tmp_path


def test_catalog_blocks_every_transcription_tool() -> None:
    assert set(TRANSCRIPTION_TOOLS) <= set(blocked_tools(load_catalog(), "audacity"))


@pytest.mark.parametrize("tool", TRANSCRIPTION_TOOLS)
def test_transcription_is_denied_for_every_agent(project: Path, tool: str) -> None:
    verdict = mcp_violation("audacity", tool, project)
    assert verdict is not None and verdict.severity is Severity.DENY


def test_editing_tools_stay_usable(project: Path) -> None:
    for tool in ("project_get_info", "label_list", "noise_reduction"):
        assert mcp_violation("audacity", tool, project) is None


def test_gateway_refuses_transcription_before_starting_the_worker(
    project: Path,
) -> None:
    connection = MCPConnection(ServerSpec(name="audacity", command="docker"), project)

    async def call() -> None:
        await connection.call("transcribe_audio", {})

    with pytest.raises(PermissionError, match="transcribe_audio"):
        asyncio.run(call())
    assert connection._task is None


def _requirement_names(text: str) -> set[str]:
    names = set()
    for line in text.splitlines():
        match = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)", line)
        if match:
            names.add(re.sub(r"[-_.]+", "-", match.group(1)).lower())
    return names


def test_no_speech_or_model_package_is_installed() -> None:
    text = (SANDBOX / "audacity-requirements.txt").read_text()
    names = _requirement_names(text)
    assert "mcp" in names
    assert not names & MODEL_PACKAGES
    # Every package is pinned exactly and hashed: nothing resolves at build time.
    blocks = re.split(r"\n(?=[A-Za-z0-9])", text)
    packages = [block for block in blocks if re.match(r"^[A-Za-z0-9]", block)]
    assert len(packages) == len(names)
    for block in packages:
        assert re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]*==\S+", block), block[:60]
        assert "--hash=sha256:" in block, block[:60]


def test_worker_build_installs_no_deps_and_proves_models_absent() -> None:
    dockerfile = (SANDBOX / "audacity.Dockerfile").read_text()
    assert "--no-deps" in dockerfile and "--require-hashes" in dockerfile
    for module in (
        "faster_whisper",
        "ctranslate2",
        "onnxruntime",
        "torch",
        "huggingface_hub",
    ):
        assert module in dockerfile


def test_worker_pins_match_versions_env() -> None:
    versions = (SANDBOX / "versions.env").read_text()
    pinned = re.search(r"^AUDACITY_DEB_VERSION=(\S+)$", versions, re.MULTILINE)
    assert pinned
    dockerfile = (SANDBOX / "audacity.Dockerfile").read_text()
    assert f"ARG AUDACITY_DEB_VERSION={pinned.group(1)}" in dockerfile
    checksums = (SANDBOX / "checksums.txt").read_text()
    for name in ("audacity", "audacitydata"):
        assert re.search(rf"^{name} [0-9a-f]{{64}}$", checksums, re.MULTILINE)
