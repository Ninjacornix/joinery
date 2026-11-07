# 0008: Sandbox image as the real creation-only boundary

Status: accepted, 2026-11-07

## Context
ADR 0002 and ADR 0006 both end at the same place: the per-agent hooks are a
guard rail, and the offline sandbox worker is what actually guarantees Rule
Zero. A hook matches text and can be talked around; a process with no network
cannot reach a generative service at all. Phase 0 also needs the creative tools
installed somewhere reproducible before any round-trip can be measured.

## Decision
`sandbox/` builds a Debian image holding the creative tools, run with no
network:

    sandbox/fetch-checksums.sh        # once, records real hashes (needs network)
    sandbox/build.sh --with blender   # verified build
    docker run --rm -it --network none -v "$PWD":/work joinery-sandbox

**Tools are selectable.** All of them together is several gigabytes, and a
project enables only some, so each tool is an opt-in build arg and its own
layer. `build.sh --all` takes everything packaged here. The default is the core
that can run a Godot project and render audio and video.

**apt where possible.** Krita, SuperCollider, Surge XT, GIMP, Inkscape,
ffmpeg, fluidsynth and xvfb come from Debian, which pins and GPG-verifies them.
Only four artifacts are downloaded directly.

**Hashes are measured, never written by hand.** `versions.env` holds the pins;
`fetch-checksums.sh` downloads each artifact and records its sha256 in
`checksums.txt`; the build verifies every download against that file. A missing
entry, an empty file or a mismatch aborts the build. A guessed hash would be
worse than none, since it would verify nothing while looking verified, so
`checksums.txt` ships empty and `build.sh` refuses to run until it is filled.

**Versions are confirmed, not assumed.** Each URL was probed before being
pinned, which caught two wrong guesses: Material Maker is at 1.8 and ships a
`.tar.gz` with no patch component in its name, and MPFB2 attaches no assets to
its releases, so its tagged source archive is used and the addon is taken from
`src/mpfb`. `fetch-checksums.sh` fails loudly on a URL that does not resolve,
so a bad version cannot be baked in silently.

**`joinery doctor`** reports which programs an enabled tool needs and whether
they are present, on the host or in the image. It never installs anything;
installing is the image's job. A user-licensed tool (Aseprite, REAPER) missing
is reported but is not a failure.

## Consequences
The image is the unit of trust for asset work, so tool MCPs that run untrusted
code (Blender Lab's included, per ADR 0003) are only safe inside it. The
container runs as an unprivileged user, since tool scripts are untrusted.

Aseprite is deliberately absent: it is paid and source-available, so the user
mounts their own licensed build, with Krita as the free pixel path.

Building needs network and several gigabytes of disk; running does not need
network and must not have it. The Surge XT Debian package name still needs
confirming on the first `--with surge` build, where it will fail loudly rather
than produce an image quietly missing the tool. `surgepy`, which PLAN wants for
rendering notes, is not installed yet.
