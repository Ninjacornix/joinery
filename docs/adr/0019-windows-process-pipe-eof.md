# Windows process-pipe EOF

Accepted: 2026-11-08. Scope: one upstream candidate patch against the pinned
Godot 4.7.2 tree, `drivers/windows/file_access_windows_pipe.cpp`.

The native Windows widget gate exercises real child-process pipes, including
script reload, a large Unicode reply, chunked stdin, cancellation and teardown.
The child completed, but polling its remaining output logged an engine error:
`PeekNamedPipe` returns `ERROR_BROKEN_PIPE` when the writer has closed normally.
Godot's available-byte query treated every failed peek as an error.

Evidence: [native lifecycle failure](https://github.com/Ninjacornix/joinery/actions/runs/42497957702).
The GDScript consumer already drains available bytes before closing its handles.
`is_open` describes the local handle, and `get_error` cannot predict remote EOF;
neither can avoid this native error reliably.

Map only `ERROR_BROKEN_PIPE` to zero available bytes and `ERR_FILE_EOF` in the
Windows pipe implementation. Preserve buffered data and existing reporting for
other failures. The graphically tested editor and native widget gates continue
to reject engine errors. Suppressing output or assuming a child cannot have
buffered bytes after exit would hide real failures or lose replies.

This general Windows I/O fix belongs in its own upstream candidate patch. The
fork-area guard permits only this patch's exact implementation file with this
ADR. It does not open the drivers directory to other changes, alter creation
policy, or admit unrelated errors as successful EOF. Native command lifecycle,
Unicode, stdin, cancellation and streaming-chat tests must pass before release.
