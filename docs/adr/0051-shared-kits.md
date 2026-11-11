# 0051. Shared kits

Status: accepted (2026-11-11)

## Context

A harbour made in one game (its rocks, crates, a pier prefab, the splash of a
wave) is useful in the next one, and friends making games together want to swap
such sets. Copying the files by hand loses what Rule Zero needs: where each
asset came from, its licence and the recipe that makes it again. A copied GLB
has no `joinery.lock` entry, its recipe points at paths that don't exist in the
new project, and a `.tscn` that uses it points at `res://` paths that moved.
Sharing must not become a way round the provenance gate, nor a download channel.

## Decision

1. **A shared kit is a `.joinerykit` file**: a zip with a `kit.toml` manifest
   (name, version, author from the user's git name, licence, description,
   Joinery version, and every file with its kind, SHA-256 and size) and the files
   under `files/`, at the paths they had in the project they came from. A kit
   holds assets, the recipes that make them with their scripts and inputs,
   prefab scenes and materials, kit generators with presets ("Harbour rock")
   and their preview thumbnails. Joinery never downloads a kit: the user moves
   the file between projects, or to a friend, themselves.
2. **Export gathers what it takes to make the choice again.** An asset brings
   its recipe, the recipe's script and inputs and every other output of that
   recipe; a scene or resource brings what its `res://` paths use; a generator
   brings its code, and a set made with it becomes a preset. Only Rule Zero's
   origins go: tool-created with its recipe, from another kit, or the user's own
   import with a licence to share it. A reference image (licensed "reference
   only, not shipped") never goes, nor anything without a lock entry, nor game
   code (`.gd`, `.cs`, native libraries): a kit is content. An asset made with a
   custom tool goes only when the user ticks "Include assets made with a custom
   tool", and stays marked in the manifest and in every lock it reaches.
   `joinery kit export --project P --name N --include … [--generator …]
   [--preset …] [--licence L] --out F.joinerykit [--plan]`; the licence is the
   game's (`[game] licence` in joinery.toml, else its LICENSE file) unless the
   user picks another.
3. **Reading a kit checks every byte before anything is written.** Names are
   plain relative paths under `files/` (no absolute path, `..`, backslash or odd
   characters); links, executable bits, encrypted entries and duplicates are
   refused; each file and the whole kit have size limits (512 MB, 2 GB, 5000
   files) and a file may not inflate more than 200 times. Every file must be in
   the manifest and match its SHA-256 and size, and every manifest entry must
   be in the zip. Python is allowed only as a recipe's script or a generator,
   and both only ever run in the offline worker, like every recipe. A
   generator's `SPEC` is read as a literal (`ast.literal_eval`), never executed
   on the host, so an imported generator has no `limits()`: a value its code
   can't build fails in the worker with Blender's message.
4. **Import is one reviewed change** (`joinery kit import --project P F
   [--into kits/<folder>] [--plan] [--yes]`). The files go under
   `kits/<folder>/` (the kit's name; a second kit with the same name gets
   `kits/<name>_2`), the recipes into `recipes/<folder>__<name>.toml` so
   `joinery rebuild` finds them. Every path of the kit inside a recipe, its
   script, a text source (a SuperCollider patch writes its own path) and a
   scene or resource is moved to match (`art/x` → `kits/<folder>/art/x`, `res://`
   too); scenes lose their `uid=` so Godot gives them new ones, and a scene
   that uses a file the kit doesn't carry is refused. A recipe's expected
   fingerprints are kept, apart from text it moved, so `joinery rebuild`
   proves the kit's assets come out the same. Each asset gets a `kit` entry in
   joinery.lock: the kit's name, version, author and licence, how it was first
   made (`made_as`), its tool and recipe here, and a human import's own
   licence. Making the lock again keeps those entries the kit's. The asset gate
   checks `kits/<folder>/art` and `audio` like the game's own. `kit.toml` is kept
   as it came and `installed.toml` records where each file went.
5. **Update and remove are reviewed changes too.** Importing a kit with a name
   already here replaces it in its own folder and shows what changed (added,
   changed, removed, version from and to). `joinery kit remove --name N` is
   refused while a scene, resource or script outside the kit, or a recipe's
   input, still uses its files, and names them.
6. **Agents use kits, users share them.** Imported generators join the kit
   catalogue as `<folder>_<id>` ("Harbour rock"), with their source and
   presets in `kit_list`, so `kit_make`, `kit_batch` and the Kits tab use them;
   a generator file changed since its import is left out. The shared hook
   refuses `joinery kit import`, `export` and `remove` to every agent (DENY,
   the turn goes on); only the user clicks Import kit…, Export kit…, Update…
   or Remove in the Kits section, or runs the command.
7. **Licences travel with the game.** The Share build adds
   `LICENSES/Kits.txt` (each kit's name, version, author and licence, and the
   licence of each import it carried), and the page's credits name each kit.
   A kit's recipes, generators and thumbnails stay out of exported builds.

## Consequences

- Kit paths nest when a kit carries files of another kit (`kits/b/kits/a/…`);
  every one of them still rebuilds and keeps its first kit as `upstream`.
- Kit settings (`kit_edit`) don't apply to a kit's own models: their recipe is
  the kit's. Make new models from its generator instead, or edit the model
  with the Blender operations.
- A recipe whose script builds a path from pieces (`os.path.join("art", …)`)
  is not moved; its rebuild then fails, which `joinery rebuild` reports. All of
  Joinery's own recipes write whole paths.
- Generators run with the importing Joinery's `kits/blender/common.py`; the
  import says so when the kit was made with a newer Joinery.
