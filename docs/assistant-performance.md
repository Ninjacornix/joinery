# Assistant modes, progress and performance

The sidebar has **Auto**, **Fast** and **Thorough** modes plus a model selector.
An optional effort selector appears when the selected model advertises reasoning
levels. Choices are saved in the editor's project metadata, not in provider
configuration or a game asset. Automatic model captions update to the requested
model; elapsed-time tooltips and expandable response details include execution
settings. No new API key is needed for an already connected subscription.

Auto sends short, recognized single-purpose requests (for example, “rename this
box”) to an available compact model with low supported reasoning. Recognized
creative/debugging work and long requests use a stronger available model with
high supported reasoning. Ambiguous requests retain the provider default.
Family-name hints rank models from the returned list; they are not entitlement
checks or measured speed rankings. Fast/Thorough force the corresponding routing
preference. An explicit model or effort overrides automatic selection. There is
no silent retry with a different model if an explicit preference is unavailable.
Stale preferences are shown as unavailable and can be reset to Automatic.
Type `/models` to refresh the provider's model list without replacing the
conversation thread.

## Provider capabilities

| Connection | Model and effort selection |
| --- | --- |
| Codex subscription | Paginated `model/list`; excludes hidden/duplicate models and uses only returned reasoning levels. `turn/start` selects model/effort while retaining the conversation thread. |
| API profiles | Codex keeps the exact verified endpoint model; opencode filters its reported choices to that verified model. Other configured accounts are not offered as substitutes. Unverified effort levels are not invented. |
| Gemini / opencode ACP | Uses reported `configOptions` model/thought selectors, including grouped options and changed reasoning levels after model selection. Falls back to the older reported model list and `session/set_model` when config options are absent. Permission modes are never changed by Fast/Thorough. |
| Claude print transport | Retains the CLI's configured model because this transport has no account model-list interface. Offers low/medium/high effort only when the installed CLI advertises `--effort`; the configured model/provider must support it. No subscription model names are invented. |
| Other providers without metadata | Retains their configured model and unsupported controls remain unavailable. |

Provider or account errors remain visible. Entitlements, rate limits and model
latency still belong to the connected provider. No end-to-end inference latency
or quality improvement is asserted from a native worker benchmark.

## Read-only specialists

The provider-facing `inspect_in_parallel` MCP tool accepts one or two named
inspection questions. Each child uses the same provider/profile, fast routing,
the shared policy hook and a separate read-only gateway endpoint. At most two
children run concurrently, with a 90-second deadline and bounded response size.
They can inspect editor context, audited native read tools, schemas and source
metadata. They cannot edit scenes/documents, approve art direction or changes,
or delegate again through the gateway. Codex native child-agent creation and
Claude Task/Agent tools are also disabled in these child sessions.
A specialist's Rule Zero hook sends its stop directly to the parent endpoint,
so a child-only refusal cannot become a retry by the main model.

The model is instructed to use this for independent inspections on complex work,
not simple edits. Child failures are returned individually; they must not be
presented as successful validation. Stop cancels children and the parent before
discarding pending changes. All mutations remain on the main thread and pass
through the existing serialized transaction/review path. Specialist model work
uses additional provider allowance; the parallelism limit is fixed at two.

## Progress and measurements

Connection state, active work, elapsed time and Stop have separate native
controls. Progress never becomes chat transcript text. Stop changes immediately
to “Stopping…” and stays pending until cleanup completes. Accepted changes still
live in the dedicated Review panel with previews above scrollable details and
actions outside the scroll area. Image previews are larger. Both light and dark
themes keep Godot's fonts/scaling, native focus behavior and existing real
provider icons. Setup shares the same theme and its tool list scrolls inside a
stable dialog instead of increasing the window's minimum height.

The sidebar follows the supplied VS Code reference: right-aligned user bubbles,
plain assistant replies, copy actions, and collapsible completion/timing details.
The larger multiline composer contains selected-resource badges, selection
attachment, mode/model/effort selectors, Send and Stop. Enter sends; Shift+Enter
adds a line. Slash suggestions float above the whole composer. The conversation
supports basic bold/inline/fenced-code formatting while escaping raw BBCode.
Streaming updates are coalesced once per frame and preserve the scroll position
when the user is reading earlier messages. No inactive window controls or voice
buttons are shown.

Hover over the elapsed time for first-response, tool connection, discovery,
tool execution, import and review durations. The initial connection frame also
reports provider connection time. Each turn writes a bounded local history (200
records) to `.joinery/latency.jsonl`. Records contain model/mode/effort, outcome,
random turn identity and durations; they contain no prompts, selected paths,
tool arguments, API keys or response bodies. Diagnostic write errors do not
invalidate project work. Phase spans can overlap: tool time includes connection
and import work, and native editing/export are bundled inside the document
operator. Do not add every phase to reconstruct the total. Review timing ends
when the preview is ready, before waiting for user acceptance.

## Reduced startup overhead

Successful tool discovery is cached for 15 seconds and invalidated when a known
connection closes. Independent server discovery starts concurrently, capped at
three. Failed readiness is not cached as success. Tool connections remain
project-owned and lazy outside explicit discovery.

Supervised first-party native operations reuse an offline container within the
same worker scope, resolved project, mount mode and image. Read-only and writable
mounts never share a container. Every operation starts a fresh native app and
reloads its document; no Blender/Krita document state is reused. A lightweight
process-group wrapper reaps application helpers. Failure, timeout or cancellation
removes that container; supervisor labels independently clean it on MCP shutdown.
Standalone unscoped operations retain the original one-shot runner. No network,
model credentials or cross-project app state is introduced into creative workers.

## Reproduce

```sh
uv run pytest -q
uv run python tests/integration/assistant_latency.py --all-native
uv run python tests/godot/run_widget_tests.py
uv run python tests/godot/run_assistant_ui_test.py
uv run python tests/godot/run_startup_test.py
uv run python tests/godot/run_scene_edit_test.py
```

Native commands require the prepared worker images/editor; graphical commands
need a display (`xvfb-run -a` on headless Linux). All checks use disposable
technical projects and no model inference. The worker benchmark writes
`out/assistant-latency.json`; assistant captures are under
`out/assistant-captures/`. CI runs native worker lifecycle/cancellation checks
and retains latency evidence; the native platform matrix captures both UI themes
at 480px and 1000px widths. Hosted runs remain pending a push.

See the [execution report](../evals/results/responsive-assistant.md) for measured
results. Protocol references: [OpenAI app-server models](https://learn.chatgpt.com/docs/app-server#list-models-modellist),
[ACP session config](https://agentclientprotocol.com/protocol/v1/session-config-options),
[Claude CLI flags](https://code.claude.com/docs/en/cli-reference).
