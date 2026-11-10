"""Images attached to a Journeyman message: checked, then passed to each provider as
context (inline where the provider takes images, by path where it doesn't)."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from bridge.acp_session import ACPSession
from bridge.claude_session import user_input
from bridge.instructions import GAME_GUIDE
from bridge.protocol import Prompt
from bridge.provider_process import prompt_text
from joinery import chat_attachments

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def attachment(project: Path, name: str = "a1.png", data: bytes = PNG) -> Path:
    folder = project / chat_attachments.ATTACHMENT_DIR
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(data)
    return path


def test_validate_accepts_only_sidebar_images(tmp_path: Path) -> None:
    first = attachment(tmp_path)
    second = attachment(tmp_path, "b.jpg", b"\xff\xd8\xff\xe0" + b"\x00" * 32)
    assert chat_attachments.validate(tmp_path, []) == ()
    assert chat_attachments.validate(tmp_path, None) == ()
    assert chat_attachments.validate(
        tmp_path, [str(first), str(second), str(first)]
    ) == (str(first.resolve()), str(second.resolve()))
    outside = tmp_path / "art/door.png"
    outside.parent.mkdir()
    outside.write_bytes(PNG)
    with pytest.raises(ValueError, match="attached in the sidebar"):
        chat_attachments.validate(tmp_path, [str(outside)])
    escaped = tmp_path / chat_attachments.ATTACHMENT_DIR / "../../art/door.png"
    with pytest.raises(ValueError, match="attached in the sidebar"):
        chat_attachments.validate(tmp_path, [str(escaped)])
    text = attachment(tmp_path, "note.txt", b"hello")
    with pytest.raises(ValueError, match="attached in the sidebar"):
        chat_attachments.validate(tmp_path, [str(text)])
    fake = attachment(tmp_path, "fake.png", b"not an image at all")
    with pytest.raises(ValueError, match="not a PNG"):
        chat_attachments.validate(tmp_path, [str(fake)])
    with pytest.raises(ValueError, match="gone"):
        chat_attachments.validate(
            tmp_path, [str(tmp_path / chat_attachments.ATTACHMENT_DIR / "x.png")]
        )
    with pytest.raises(ValueError, match="up to 5"):
        chat_attachments.validate(tmp_path, [str(first)] * 6)
    with pytest.raises(TypeError):
        chat_attachments.validate(tmp_path, "a.png")


def test_media_types_come_from_the_bytes(tmp_path: Path) -> None:
    webp = attachment(tmp_path, "w.webp", b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 8)
    media, data = chat_attachments.encoded(webp)
    assert media == "image/webp"
    assert base64.b64decode(data) == webp.read_bytes()


def test_every_agent_is_told_images_are_references_not_assets() -> None:
    assert chat_attachments.NOTE in GAME_GUIDE
    assert "not assets" in GAME_GUIDE


def test_text_providers_get_paths_and_inline_providers_do_not(tmp_path: Path) -> None:
    path = str(attachment(tmp_path))
    prompt = Prompt(text="Look", project_dir=str(tmp_path), attachments=(path,))
    assert path in prompt_text(prompt)
    assert "not assets" in prompt_text(prompt)
    assert path not in prompt_text(prompt, images_inline=True)
    plain = Prompt(text="Look", project_dir=str(tmp_path))
    assert "Attached images" not in prompt_text(plain)


def test_claude_takes_images_as_stream_json_content(tmp_path: Path) -> None:
    path = attachment(tmp_path)
    plain = Prompt(text="Hello", project_dir=str(tmp_path))
    assert user_input(plain).decode().endswith("Hello")
    message = json.loads(
        user_input(
            Prompt(text="Hello", project_dir=str(tmp_path), attachments=(str(path),))
        )
    )
    assert message["type"] == "user"
    text, image = message["message"]["content"]
    assert text["type"] == "text" and text["text"].endswith("Hello")
    assert image["type"] == "image"
    assert image["source"]["media_type"] == "image/png"
    assert base64.b64decode(image["source"]["data"]) == PNG


def test_acp_sends_images_only_when_the_agent_takes_them(tmp_path: Path) -> None:
    path = str(attachment(tmp_path))
    prompt = Prompt(text="Hello", project_dir=str(tmp_path), attachments=(path,))
    session = ACPSession("gemini")
    blocks = session.prompt_blocks(prompt)
    assert [b["type"] for b in blocks] == ["text"]
    assert path in blocks[0]["text"]
    session.image_input = True
    blocks = session.prompt_blocks(prompt)
    assert [b["type"] for b in blocks] == ["text", "image"]
    assert blocks[1]["mimeType"] == "image/png"
    assert path not in blocks[0]["text"]
