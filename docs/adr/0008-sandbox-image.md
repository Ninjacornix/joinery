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

**apt where possible.** Krita, SuperCollider, GIMP, Inkscape, ffmpeg,
fluidsynth and xvfb come from Debian, which pins and GPG-verifies them. Only
four artifacts are downloaded directly. Every package name was checked against
Debian trixie rather than assumed; Krita is 5.2.9 and SuperCollider 3.13.0,
both above what PLAN requires.

**A tool that the OS does not package is replaced, not forced.** Surge XT is
not in Debian, and upstream publishes only nightly Linux builds under a moving
`Nightly` tag, so there is no stable artifact to pin or hash. Rather than
compile it or pin a nightly whose hash changes daily, the sandbox synthesises
with SuperCollider, which is packaged, drives headlessly from a script, and is
what the catalog's audio MCP already targets. MIDI renders through FluidSynth
with the Fluid (R3) GM SoundFont, MIT-licensed, which also closes a real gap:
fluidsynth with no SoundFont has no instruments to play. Debian also packages
zynaddsubfx, amsynth and yoshimi if a subtractive synth is wanted later; none
is installed until something drives it.

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
they are present. It never installs anything; installing is the image's job,
and doctor points at the image when something is missing. A user-licensed tool
(Aseprite, REAPER) missing is reported but is not a failure.

## Measured
Built and verified on 2026-11-07, amd64 emulated on an arm64 host:

| | |
|---|---|
| Image, core + Blender + SuperCollider + Material Maker | 4.07 GB |
| Build time, warm base layer | 4m 11s |
| Godot | 4.7.2.stable.official.ed1daf0bf, matching `UPSTREAM` |
| Blender | 5.2.1 LTS, with the MPFB2 addon in place |
| SuperCollider | sclang 3.13.0 |
| FluidSynth / ffmpeg | 2.4.4 / 7.1.5 |

Checked by doing the work, not by reading version strings, all with
`--network none`: Blender added a bevelled cube and saved a 96 KB `.blend`;
FluidSynth rendered a MIDI triad through the GM SoundFont to a 44.1 kHz stereo
WAV; Godot imported a project headless; Blender ran under Xvfb. The container
runs as the unprivileged `joiner`, and `deb.debian.org` is unreachable from it.

## Consequences
The image is the unit of trust for asset work, so tool MCPs that run untrusted
code (Blender Lab's included, per ADR 0003) are only safe inside it. The
container runs as an unprivileged user, since tool scripts are untrusted.

Aseprite is deliberately absent: it is paid and source-available, so the user
mounts their own licensed build, with Krita as the free pixel path.

Building needs network and several gigabytes of disk; running does not need
network and must not have it. `surgepy`, which PLAN names for rendering notes
through Surge XT, is therefore not installed either; the SuperCollider path
replaces it, and PLAN's sound stage should be read that way.

The image is amd64 because Blender and Material Maker publish no Linux arm64
build, which also matches CI. On an arm64 host it runs emulated, where a long
run of package fetches drops connections part way through a layer; `apt-install`
prefers IPv4, disables pipelining, serialises per host and retries the whole
step, since apt keeps what it already downloaded. Without that the build fails
roughly every time on Apple Silicon. Emulated Blender is slow, so interactive
work belongs on the host via live mode.

The image carries the creative tools, not Joinery's Python environment. Running
the MCP servers inside it, which is where they have to run to be genuinely
offline, needs that environment added and is the next piece of sandbox work.
