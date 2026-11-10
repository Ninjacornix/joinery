"""Project art direction is approved by content hash, never inferred from existence.

Two acts record an approval, both the user's: the Approve button in /art (or
`joinery bible --approve`), and making the project from the user's own answers.
A bible drafted from those answers counts as approved at creation, so art and
audio work can start at once. Any later change to the bible's bytes lapses the
approval and the user approves again with one click. Neither act is a model
tool: the gateway and registry have no approval operation.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from joinery.transactions import atomic_json


def fingerprint(project: Path) -> str:
    path = project / "art/bible.md"
    if not path.is_file():
        raise ValueError("The project has no art bible.")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def approved(project: Path) -> bool:
    try:
        data = json.loads((project / ".joinery/art-approval.json").read_text())
        return data.get("bible_sha256") == fingerprint(project) and bool(
            data.get("approved_by")
        )
    except (OSError, ValueError):
        return False


def review(project: Path) -> dict[str, str | bool]:
    """Bind the approval button to precisely the content displayed to the user."""
    content = (project / "art/bible.md").read_bytes()
    return {
        "kind": "art_review",
        "text": content.decode(),
        "sha256": hashlib.sha256(content).hexdigest(),
        "approved": approved(project),
    }


def approve(project: Path, approved_by: str, expected_sha256: str = "") -> None:
    """Called only by an explicit user approval action, not a model tool."""
    if not approved_by.strip():
        raise ValueError("Identify the person approving the art direction.")
    content = (project / "art/bible.md").read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    if expected_sha256 and expected_sha256 != digest:
        raise ValueError(
            "The art bible changed while being reviewed. Open its current version before approving."
        )
    text = content.decode()
    if "Not set yet" in text or "{{" in text:
        raise ValueError("Finish the art direction before approving it.")
    atomic_json(
        project / ".joinery/art-approval.json",
        {
            "version": 1,
            "bible_sha256": digest,
            "approved_by": approved_by.strip(),
        },
    )


#: Who approved a bible drafted from the user's answers when the project was made.
AT_CREATION = "user (at project creation)"
#: What the user is told when that happens.
APPROVED_AT_CREATION = (
    "Wrote the art bible from your answers. It counts as approved, so art and "
    "audio work can start. If it changes later, approve it again in /art."
)


def approve_at_creation(project: Path) -> bool:
    """Approve a bible just drafted from the user's own answers at project creation.

    Called only by project setup right after it renders those answers, never
    by a model tool. Records exactly what the Approve button records. A draft
    with required sections still unset is left for the user to finish and
    approve, as the button would refuse it too. Returns whether it is approved.
    """
    try:
        approve(project, AT_CREATION)
    except (OSError, ValueError):
        return False
    return True
