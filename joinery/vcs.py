"""Git with LFS, set up by default for every Joinery project.

A Joinery project keeps its native sources in the repo: `.blend` files, `.kra`
paintings, rendered WAVs, baked PNGs. Those are binaries, and committing them
to plain git bloats history permanently and makes every clone pay for every
revision ever made. LFS is the difference between a repo that stays usable for
years and one that does not, and it has to be in place *before* the first big
file is committed, because converting afterwards means rewriting history.

So it is not an option presented at creation time; it is simply how a project
starts. Godot's own Version Control Metadata writes a `.gitignore` and a
`.gitattributes`, and this adds the LFS rules to whatever is there rather than
replacing it, so nothing Godot wrote is lost.
"""

from __future__ import annotations

from pathlib import Path

from joinery.catalog import ROOT

ATTRIBUTES = ".gitattributes"
MARKER = "# Native tool sources and binaries via Git LFS"


def lfs_rules() -> str:
    """The engine repo's own LFS rules, so projects track what Joinery tracks."""
    source = ROOT / ATTRIBUTES
    if source.is_file():
        return source.read_text().rstrip("\n") + "\n"
    # A Joinery install without its own .gitattributes should still produce a
    # correctly configured project, so these are the same formats by hand.
    lines = [MARKER]
    for suffix in (
        "blend",
        "kra",
        "aseprite",
        "png",
        "exr",
        "hdr",
        "wav",
        "ogg",
        "glb",
        "sf2",
    ):
        lines.append(f"*.{suffix} filter=lfs diff=lfs merge=lfs -text")
    return "\n".join(lines) + "\n"


def ensure_lfs(project: Path) -> bool:
    """Add the LFS rules to a project's .gitattributes. Returns True if changed.

    Appends rather than overwrites: Godot's VCS metadata step may have written
    this file first, and clobbering it would throw away whatever it set up.
    """
    path = project / ATTRIBUTES
    existing = path.read_text() if path.is_file() else ""
    if MARKER in existing:
        return False
    merged = existing.rstrip("\n")
    merged = (merged + "\n\n" if merged else "") + lfs_rules()
    path.write_text(merged)
    return True
