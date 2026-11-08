# 0014: Connect an agent before opening projects

Status: accepted, 2026-11-08

## Decision

The Project Manager starts with agent connection, before project actions. The
user selects Claude Code, Codex, Gemini or opencode from a vertical list with
existing provider marks imported from local apps/extensions. A selected agent
with local sign-in detected advances automatically; Sign in again and Continue
are removed. A configured environment API key requires explicit activation.
The screen has no skip action; project creation/import/open shortcuts
also wait for it. The chosen agent is remembered and inherited by launched editors.
Users can return through Change agent in the project list. That action opens
selection/settings without immediately redirecting back through the active account.

Account authentication belongs to the agent CLI. Subscription sign-in opens the
provider's normal terminal/browser flow; opencode chooses its provider in its
own /connect interface. Existing environment API keys are reused. Codex receives
its key through stdin, never argv. Joinery stores the selected agent and method,
not subscription credentials. Status output contains only readiness and method labels; account
identifiers, credential values and raw provider output stay out of the UI/logs.
These checks detect local configuration; they do not issue billable model requests
or establish a subscription's entitlement remotely. Chat transports remain unfinished.

API settings live in a submenu. Users can enter an endpoint, model ID and masked
API key, choosing an existing gated Codex or opencode driver. Profiles live in
`~/.config/joinery/connections.json` with mode 0600 outside project repositories.
The UI/helper passes JSON over stdin using bounded nonblocking pipe writes; keys
are never in argv, public status data or logs. Saved metadata can be edited
without displaying the key. An endpoint change requires entering its key again. Save verifies the supplied key
and selected model through the endpoint's `/models` list before persisting or
advancing. Failed authorization, unavailable models and redirects remain blocked;
authorization headers are never forwarded through redirects.
Codex configuration targets Responses-compatible endpoints; opencode's provider
configuration targets OpenAI-compatible APIs. Runtime launch settings use a
separate key environment variable without replacing normal CLI login credentials.
This configures a model connection; the existing transport limitation still applies.

The native shell loads the connection control from the addon before any project
is loaded. Status helpers use the same polled native subprocess pipes as the
editor workspace; no script worker threads are introduced.

## Project creation

Renderer options occupy a horizontal row. All setup steps use one centered window
rectangle, a scrollable body, consistent spacing, a progress label and Back/Next
navigation. Answers remain intact when navigating back.

Preferred MCPs are selected by default. An application without a preferred MCP
displays its first catalog candidate, while remaining unchecked. Alternatives
are marked not preferred and remain selectable as project-owned custom MCPs.
Their executable and JSON argv must be supplied explicitly; setup writes their
registration without installing or running them. Preferred admission rules and
the shared policy gate remain unchanged.

Structured choices travel through a temporary JSON file, avoiding Unix shell
quoting of user-supplied command data. The pinned upstream FileAccess::create_temp
implementation writes through its error pointer even on success; callers must
supply it. Omitting it caused the reported test-process crash at final Create.
The corrected path is covered by actual Project Manager creation of a temporary
project, including saved custom MCP argv and the bible answers.

## Sources and validation

Login choices follow the provider documentation and installed CLI help:
[Codex](https://learn.chatgpt.com/docs/auth),
[Claude Code](https://code.claude.com/docs/en/authentication),
[Gemini CLI](https://geminicli.com/docs/get-started/authentication/),
[opencode](https://opencode.ai/docs/cli/commands/).

Python tests cover sanitized status, missing CLIs, timeouts, environment keys,
stdin-only key login, literal argv registration and invalid-choice preservation.
The native UI test verifies connection gating, icons and vertical choices,
shortcut handling, horizontal renderers, stable window geometry, Back retention,
and complete project creation without launching an editor or an MCP.
Additional tests cover automatic redirection, the API submenu, stdin submission,
private file permissions, saved key reuse, endpoint changes, runtime argument
separation and hashes of the unmodified local provider marks.
