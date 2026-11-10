# Project MCP gateway and native-document transactions

The sidebar previously offered a box/transform tool while preferred MCP choices
were only catalog entries. Authentication and a visible tool icon therefore did
not establish that an operation could run. The editor now owns one project
gateway shared by all provider transports.

The gateway supervises pinned MCP servers, discovers their actual schemas and
applies the shared policy on every call. Raw provider filesystems are read-only.
Codex uses its app-server; Claude uses its print/stream-json transport;
Gemini/OpenCode use ACP v1. The provider-facing MCP proxy exposes discovery,
schema lookup, calls and live-editor requests. It has no approval endpoint.
Godot, official Blender, SuperCollider and Inkscape installations retain exact
source revisions and dependency locks. Upstream v1 servers use the SDK's legacy
initialize handshake; first-party servers use the current protocol.

Live scene operations use stable instance identities and native undo. Saved
files and native creative documents operate in a project copy. Original files
are retained in a durable transaction journal. Review binds the exact diff;
accept checks both working-file conflicts and changes to staging after review.
A project lease prevents another authoring host from recovering an active
transaction. A new host restores interrupted commits and discards abandoned
previews. Accepted file changes have a checked `transactions revert --id` path;
live scene changes use the editor's undo history.

Review controls live in a dedicated bottom panel, separate from chat. Previews
appear before scrollable file details; Accept and Reject remain outside the
scroll area. Accepted model exports open in the asset viewer, which retries the
preview when Godot finishes importing. Exporting alone does not instantiate the
asset into a game scene. Command completion uses its own canvas layer so dock
layout and clipping cannot cover it.

Provider discovery ranks query words and returns compact readiness summaries.
Schema lookup starts only the requested server. The proxy forwards native MCP
results directly, preserving failure flags, while anticipated first-party errors
use the SDK's explicit ToolError path. This avoids hiding the reason behind a
generic error and nesting tool output inside another successful result. Work
phases appear in the status line rather than as chat logs.

Creative workers have no network and receive the staged project without model
credentials. App processes and nested jobs are labelled and cleaned up by their
owning connection. Cancellation does not fall back to a host app. A typed source
part transform and the native-document operator save `.blend`, export GLB and
record measured replay/provenance. Krita uses a disposable PyKrita main-thread
worker; the supervisor stops it after a completed save/export acknowledgement.
Material Maker is driven through its packaged native code and matching Godot
runtime with software Vulkan. SuperCollider uses native NRT scores. (The
Aseprite Lua path was removed with Aseprite; see ADR 0025.)

Incremental recipes preserve an immutable native preimage. Superseded recipes
are archived; the source registry resolves current editable documents ahead of
replay inputs. Blender fingerprints include geometry, transforms and materials.
Krita fingerprints compare layer/pixel data and canonical XML while excluding
transient document UUIDs and save dates. Export formats use exact hashes where
deterministic. Existing human-import/custom provenance is preserved.

Art approval records a hash of the completed project bible. Editing its content
invalidates approval. The native `/art` action and explicit CLI approval are user
actions; model MCP tools cannot approve direction or accept transactions.

Addon protocol version 2 rejects stale copied addons before a model turn.
Updates require a closed project editor and keep a backup. Source/engine bundles
include a measured manifest and matching build version; diagnostics include the
binary UUID without provider credentials. Live provider verification is tracked
separately from transport code and fixture tests.

Protocol references: [Claude print mode](https://code.claude.com/docs/en/headless),
[ACP session setup](https://agentclientprotocol.com/protocol/v1/session-setup),
[OpenCode ACP](https://docs.opencode.ai/docs/acp/).
