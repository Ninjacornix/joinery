"""Images the user attaches to a Journeyman message, and the editor views it shares.

They are reference context for the model only. The sidebar saves them under
`<project>/.joinery/attachments/` (kept out of git and out of Godot's import), never
under `art/`, and nothing here copies them anywhere else: an attached image is never
an asset, a source or a recipe input.
"""

from __future__ import annotations

import base64
from pathlib import Path

#: Where the sidebar saves attachments, relative to the game project.
ATTACHMENT_DIR = ".joinery/attachments"
#: Images per message; the sidebar enforces the same limit.
MAX_IMAGES = 5
#: The sidebar scales images to 2048 px on the long side; anything far larger is not one of its files.
MAX_BYTES = 12_000_000
MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
#: One sentence every agent is told (bridge.instructions).
NOTE = (
    "Images the user attaches are references from the user for that message, not assets: "
    "never copy them into the project, trace them into art or treat them as sources."
)


def sniff(data: bytes) -> str:
    """The media type the file's own bytes declare, or "" when it is not PNG, JPEG or WebP."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return ""


def validate(project: Path, paths: object) -> tuple[str, ...]:
    """The absolute paths of a message's attachments, after checking each one.

    Only images the sidebar saved for this project are accepted: a file under the
    attachment folder, with an image suffix and image bytes, of a sane size.
    """
    if paths is None or paths == []:
        return ()
    if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
        raise TypeError("Message attachments are invalid.")
    if len(paths) > MAX_IMAGES:
        raise ValueError(f"Attach up to {MAX_IMAGES} images to one message.")
    root = (project / ATTACHMENT_DIR).resolve()
    accepted: list[str] = []
    for raw in paths:
        path = Path(raw).resolve()
        if not path.is_relative_to(root) or path.suffix.lower() not in MEDIA_TYPES:
            raise ValueError(
                "Only images attached in the sidebar can go with a message."
            )
        if not path.is_file():
            raise ValueError("An attached image is gone. Attach it again.")
        if path.stat().st_size > MAX_BYTES:
            raise ValueError("An attached image is too large. Attach a smaller one.")
        with path.open("rb") as stream:
            if not sniff(stream.read(16)):
                raise ValueError("An attachment is not a PNG, JPEG or WebP image.")
        accepted.append(str(path))
    return tuple(dict.fromkeys(accepted))


def encoded(path: Path) -> tuple[str, str]:
    """(media type, base64 data) for providers that take images inline."""
    data = path.read_bytes()
    media = sniff(data[:16])
    if not media:
        raise ValueError("An attachment is not a PNG, JPEG or WebP image.")
    return media, base64.b64encode(data).decode("ascii")


def listed(paths: tuple[str, ...]) -> str:
    """The attachments as text, for providers without image input."""
    if not paths:
        return ""
    return (
        "\n\nAttached images (references from the user, not assets; open them to look):\n"
        + "\n".join("- " + path for path in paths)
    )
