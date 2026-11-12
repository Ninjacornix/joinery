# 0083. Updating Joinery from inside it, and signing what it installs

Status: accepted (2026-11-12)

## Context

Joinery ships as one archive per platform on GitHub Releases. Updating meant
finding the release page, downloading the right archive, unpacking it over the
old folder and remembering to update each project's `addons/joinery`. Nothing
checked that the archive was the one CI built: a `.sha256` next to an archive
only proves the two files came from the same place.

An updater that downloads and runs code is the most attractive thing in
Joinery to attack, so it has to verify before it unpacks, never act on its
own, and work the same when no Apple or Microsoft account exists yet.

## Decision

1. **One CLI, three front ends.** `joinery update` (`joinery/updater.py`,
   `joinery/update_cli.py`) does all the work; the editor dialog
   (`godot_addon/joinery/update_dialog.gd`, Help ▸ Check for Joinery updates…
   and Tools ▸ Joinery: Check for updates…) and the Project Manager's "Check for
   updates" link (fork patch, `_joinery_add_updates`) only run it with `--json`
   and show the answer. The Project Manager loads the same dialog script from
   `JOINERY_HOME`, as it loads the connect screen.
2. **Checking.** `--check` asks the GitHub Releases API at most once a day; the
   answer is cached with the date. Offline and rate-limited answers come back
   as `offline: true`, never as an error, and do not count as a check.
   Pre-releases are offered only after the user turns them on; `--dismiss V`
   stops the daily check offering V. A source checkout (CI, a developer's
   editor) never asks GitHub on its own: only an explicit check (`--force`,
   the menu items) does, and it never installs ("update it with git pull").
3. **Nothing is downloaded until the user presses Download and install.** The
   quiet daily check opens the dialog only for a new, signed, not dismissed
   version; otherwise it only changes the link's text.
4. **Verify, then unpack.** `--install` downloads this platform's archive only,
   checks its `.sha256`, then its Sigstore bundle (`.sigstore.json`) with the
   pinned `sigstore` library: the certificate must name
   `https://github.com/irchiinnuss/joinery/.github/workflows/release.yml@refs/tags/vX`,
   the GitHub Actions token issuer, this repository and that tag. A release
   without a bundle is shown but not installed (the dialog offers its page).
   Unpacking refuses absolute, `..` and symlinked members (`ditto` on macOS, to
   keep the code signature), and the unpacked folder must hold a
   `manifest.json` naming this version and platform.
5. **The swap happens after Joinery exits.** A copy of `update_swap.py`
   (standard library only, `python -I`, run from outside the install) waits for
   the editor and anything else running from the install, moves the old install
   to `updates/previous/<version>` (one kept), moves the new one into place,
   restores the old one if a move fails, runs the new install's
   `joinery update-addon` on each open project, and relaunches only when the
   user pressed Restart now. Restart now closes the editor the normal way, so
   unsaved scenes are asked about. Go back to <version> is the same swap with
   the kept version.
6. **Signing in CI, tags only.** `release.yml` signs every archive with
   Sigstore keyless signing from the workflow's OIDC identity, verifies each
   one with `joinery verify` before publishing, adds GitHub build provenance,
   and says in the release notes exactly what is signed. macOS Developer ID
   signing and notarization and Windows Authenticode (Azure Trusted Signing or
   a PFX) run only when all of their secrets are set (`docs/signing.md`);
   without them the notes say "ad-hoc / not notarized / not Authenticode".

## Consequences

- An attacker needs the repository's release workflow on a tag to produce an
  archive the updater accepts; a compromised download host or a swapped
  `.sha256` is not enough.
- Sigstore verification uses the public-good instance and needs the network
  (or a cached trust root for `joinery verify --offline`).
- Projects are updated by the new Joinery, so `update-addon` changes ship with
  the version that needs them.
- Not yet proven: a real signed release end to end, the Windows swap with
  locked files, notarizing a `.app` whose main executable is a script, and
  Linux process detection on a real install.

## Alternatives considered

- **A hand-written Sigstore verifier.** Rejected: the certificate chain, Rekor
  inclusion proofs, signed timestamps and the identity policy are exactly what
  should not be written by hand. The same pinned library signs in CI.
- **Minisign/GPG with a key in the repository.** Rejected: a long-lived key to
  guard and rotate, and nothing ties a signature to the workflow that built it.
- **Swapping files while Joinery runs.** Rejected: open files are locked on
  Windows and the running editor would load a mix of versions.
