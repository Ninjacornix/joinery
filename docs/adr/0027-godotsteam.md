# GodotSteam: one pinned, hash-checked download

The steam feature wraps the GodotSteam GDExtension (achievements, stats, rich
presence, friend lobbies, Steam networking). Until now the user had to install
the extension themselves. The user approved one exception to "Joinery never
downloads code into a game": a pinned GodotSteam release, downloaded only into
projects that have Steam switched on.

**The source.** GodotSteam is an MIT-licensed code library (Steamworks
bindings), published at <https://codeberg.org/godotsteam/godotsteam>. It is
not a generative tool and makes no assets. The GDExtension release
`v4.23-gde` (GodotSteam 4.23, Steamworks 1.65, loads in Godot 4.4 and later)
was chosen because it was the newest release at least two weeks old when it
was pinned; it ships `SteamMultiplayerPeer` inside the same extension, so
there is no second download. Newer releases are not picked up on their own.

**Pinning.** `policy/godotsteam.toml` records the name, version, tag, URL,
size, SHA-256, licence, the source commit and the archive layout. Changing
the release is a reviewed change to that file: download the archive once,
record its hash, and check the names the feature uses against that release's
source (see below).

**The hash check.** `joinery/godotsteam.py` downloads over https into a
per-user cache (`~/.cache/joinery/godotsteam/<version>/`), never into the
engine repo. The SHA-256 is checked before the archive is opened, and again
when a cached copy is reused. A mismatch deletes the file and installs
nothing. A redirect is followed only to an https URL the host denylist
allows, and no more than the pinned size is read. Only entries under
`addons/godotsteam/` are extracted; an absolute
path, a `..` or a symlink anywhere in the archive refuses the whole archive.
The release's own editor plugin (`plugin.cfg`, `godotsteam_plugin.gd`,
`editor/`) is left out: it checks the Godot Asset Library for newer versions
and can replace the extension with an unpinned, unchecked download. The
extension does not need it.

**Licence recording.** The release's `license.md` is written as
`addons/godotsteam/LICENSE`, and `joinery.lock` gets an `[[extensions]]`
entry: name, version, URL, archive SHA-256, licence, `origin =
"pinned-download"`, the install folder and a hash of its files. Extensions are
code, so they are never counted as assets (Rule Zero's asset origins are
untouched), but `joinery check` fails when the installed files no longer
match that hash, or when the entry's name, URL and SHA-256 are not a release
the repo pins; installing again restores them from the cache. An extension
entry can never sit under `art/` or `audio/`, so it cannot hide an asset from
the provenance gate.

**Steam-only.** The download happens when a project is created with Steam on
or when `joinery feature add steam` runs, and only when `joinery.toml [game]
features` lists `steam`; any other project is refused before a URL is
opened. Removing the feature removes the folder and the lock entry (a
folder with no lock entry, which the user put there, is left alone). Offline,
the project is still made and the game still runs (every Steam call is a
quiet no-op), with one plain message that names the command to try again:
`joinery feature add steam --project <path>`. On a project that already has
Steam on, that command only downloads the extension; there is nothing to
review.

**Settings.** The new-project questionnaire's Steam answers go to
`joinery.toml [game.steam]` (`app_id`, `connect`) and to project.godot
`steam/app_id` and `steam/connect`. `connect = "steam"` lets a hosted or joined
Steam lobby run online play over Steam networking (`SteamService` installs
the Steam transport as `Net`'s transport); `"direct"` keeps ENet only. An app
ID of 0 means "add it later".

**Names checked against the release.** The method, signal and peer names the
wrapper calls are listed in `steam_service.gd` (`INIT_METHODS`, `METHODS`,
`SIGNALS`, `SIGNAL_ARGS`, `PEER_METHODS`) and were checked against the pinned
commit's `godotsteam.cpp` and `godotsteam_multiplayer_peer.cpp`. That check
corrected one: `steamInitEx` and `steamInit` take `(app_id,
embed_callbacks)`, not a leading `retrieve_stats`. A project with the
extension can confirm the names offline with
`res://tests/features/steam/extension_check.gd`; the blueprint tests run it
when the pinned archive is already in the cache.

**Nothing is published.** Joinery never uploads, publishes or submits
anything to Steam: no builds, depots, store pages, achievement definitions
or stats. Those stay in the user's own Steamworks account and tools.
