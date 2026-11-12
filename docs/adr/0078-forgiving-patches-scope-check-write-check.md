# 0078. Forgiving patches, a scope check for raw scripts, and Godot's check after each write

Status: accepted (2026-11-12)

## Context

Three small gaps made agents spend extra turns on the same edit:

- A `write_file` patch (`patch: [{find, replace}]`, `joinery/text_patch.py`)
  failed when the `find` differed from the file only in trailing spaces,
  indentation or a curly quote the model typed for a straight one. The agent
  had to read the file again and resend.
- Raw document scripts (bpy, Krita/GIMP Python, sclang through
  `operate_document`) run in the offline worker, which confines them, but a
  script that opens sockets, starts programs or names files outside the stage
  is not doing a document operation. It failed inside the app, if at all, with
  an error that did not say why.
- A GDScript or shader parse error surfaced at import, precheck or submit,
  several steps after the write that caused it, when the agent had moved on.

Other coding agents solve the same problems: codex's `seek_sequence` matches
patch context with growing tolerance, SWE-agent lints each windowed edit and
shows only what it broke, and aider bounds its fix-the-lint retries
("reflections").

## Decision

1. **Forgiving finds** (`joinery/text_patch.py`). A `find` is looked for
   exactly first. If it isn't found, it is compared line by line, ignoring
   trailing whitespace, then indentation, then look-alike punctuation (curly
   quotes, dashes, odd spaces). Each tier still needs exactly one match, and
   no tier equates different tokens. Carried-over lines keep the file's own
   text, and new lines take the file's indentation. The write result names the
   tolerance a pair needed, and a miss shows the closest lines.
2. **Scope check before raw scripts run** (`joinery/script_scope.py`, called
   from the gateway before the app starts). A static check refuses code that
   imports what no document operation needs, opens sockets, starts programs,
   names files outside the stage or builds code at run time (`exec`, `eval`,
   `.interpret`). The refusal gives a plain reason and denies that one call, so
   the agent rewrites the script. It is not the security boundary (the worker
   is), and Rule Zero's denylist still runs first and stops the turn.
3. **Godot's check after each write** (`joinery/write_check.py`, wired into
   the gateway's staged `godot` `write_file`). When a write stages a `.gd`, a
   `.tscn`/`.tres` with built-in scripts or a `.gdshader`, Godot checks it at
   once in the offline worker, never on the host, because a project's `@tool`
   scripts run when Godot opens it:
   - GDScript goes to Godot's language server, from a headless editor kept warm
     on a read-only mount of the stage. It is started when the stage first uses
     Godot and restarted when a new `class_name` appears. If it isn't up in
     time, the file goes to `--check-only` instead.
   - Shaders go through one headless run that parses the old and the new text.
   - Only the problems this write **added** are appended after the tool result
     ("Fix them now, before anything else"), listing at most 12.
   - After 3 writes of one file in a row that add errors, the result says to
     stop patching and rethink, or to tell the user.
   - The span records the backend, seconds, new errors, new warnings and the
     streak.
   - Anything that goes wrong leaves the write as it was and says it was not
     checked. Precheck still runs every check at submit.
   - Each stage has its own check, which is closed when the change is
     accepted, rejected or shut down.

All three live in the shared gateway, so every supported agent gets them.

## Consequences

- Fewer re-reads and resent patches. A forgiven match is said in the result,
  so a wrong guess is visible in review.
- Some legitimate scripts may be refused (a module not on the allowlist).
  The fix is to widen the allowlist in `script_scope.py`, not to bypass the
  check.
- The first GDScript check of a stage waits up to 20 s for the language
  server; later checks take milliseconds. Each staged change keeps one worker
  container alive until it is decided.
- A check that falls back to `--check-only` reports only the first parse
  error and cannot compare against the old text.
