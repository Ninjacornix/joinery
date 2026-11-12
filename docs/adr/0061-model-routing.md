# 0061. A light model for light work, and reviews by risk

Status: accepted (2026-11-12)

## Context

Every agent turn Joinery starts runs on the chat's model, whatever it does.
Some turns are light: the reviewer reading a texture recolour the palette
check already passed, the session summary written before a long chat
continues fresh (`bridge/compaction.py`), the accessibility and performance
agents wording one fix (`bridge/check_runner.py`). Work on model cascades
(FrugalGPT) and learned routers (RouteLLM) shows a cheaper model can take most
such calls when a stronger one takes over whenever the cheap one is unsure.

Some changes need no reviewer agent at all: when Joinery's deterministic
checks judge every file of the change completely (an existing texture
recoloured within the palette, a loudness fix of an existing sound), the
reviewer adds a minute and tokens and finds nothing the checks did not.

Today the reviewer is on or off (`[agents] reviewer`) with an optional model
(`reviewer_model`); the author's selection `{mode, model, effort}` reaches it
otherwise (ADR 0037). Timeline entries are the author's own submit summary:
no separate model call writes them, so there is nothing to route there.

## Decision

1. **`[agents] light_model`** in joinery.toml: one name for every provider
   (`light_model = "haiku"`) or one per provider
   (`light_model = { claude = "haiku", codex = "gpt-5-mini" }`). Unset (the
   default) keeps every turn on the main model, as before. The name is passed
   as the turn's `selection.model`, so the host's `route` checks it against
   the provider's discovered models like any other choice; a name the
   provider does not list fails the light pass, which escalates (below).
2. **Where it is used**, all in the shared core, so every provider gets it:
   - the reviewer's first pass on a **low-risk** change (point 4);
   - the session summary before a fresh session;
   - the accessibility and performance agents' wording of a fix.
3. **Escalation.** A light pass never decides alone. The reviewer's light
   pass escalates when it sends the change back (`block`), warns, gives no
   findings, errors or runs out of time (`PeerReview.unsure`); the main model
   then reviews the same request, and its notes are the ones the author and
   the user see (`reviewer.escalated: true`). A light pass that objects never
   reaches the author. The summary and the check wording escalate when the
   light model gives no text (or `NONE`). The light reviewer pass has 45 s of
   its own; the main pass keeps `review_seconds`.
4. **`[agents] review = "always" | "risky" | "never"`**, default `"always"`.
   `"risky"` skips the reviewer agent when the staged change is low risk;
   `"never"` skips it for every change. A skipped review opens with
   `reviewer.status = "skipped"` and a message that says why ("Reviewer
   skipped: Joinery's checks cover this change (texture edit)."). The
   existing on/off switch (`reviewer = false`) still wins.
5. **Risk is read from the staged change** (`joinery.routing.change_risk`), the
   diff the checks just passed, not from the request's words:
   - **low** when every file is one the checks judge completely: an existing
     image or paint document modified (palette, size), an existing sound
     modified (loudness, format), material values (`.tres`, `.mmg`, `.ptex`),
     a scene of at most 12 nodes modified, plus the bookkeeping that goes with
     them (`joinery.lock`, `recipes/`, `.import`, `.uid`);
   - **high** for code (`.gd`, `.cs`, shaders, `.py`, …), a scene with more
     than 12 nodes, a new scene, any model (`.blend`, `.glb`, … new or
     modified: geometry is not judged completely), the art bible, the game
     brief or anything under `design/`, `project.godot`, `joinery.toml`,
     export presets, any deletion, any other new file, anything unrecognised,
     and more than 8 files at once. A request whose risk was not measured is
     high.
6. **Metrics** (ADR 0054). Each role turn's span carries
   `joinery.model.tier` ("light" or "main") beside the model that ran
   (`gen_ai.request.model`, `gen_ai.response.model`). The reviewer span carries
   `joinery.review.risk`, `joinery.review.route` ("skip", "light", "main") and
   `joinery.review.escalated`. `joinery metrics` lists per change which model
   ran which role's turns and tier, the review routes and escalations.
7. **Settings.** `joinery reviewer --review always|risky|never`,
   `--light-model NAME` (empty clears) and `--provider claude|codex|gemini|opencode`
   (one provider's light model); `--json` reports `review` and `light_model`
   beside the existing fields. The Settings page keeps its Reviewer switch and
   model; it does not show the new two yet (a new row with its own tests and
   screenshots), so they are set in joinery.toml or with the CLI.

## Consequences

- With no `light_model` and `review = "always"` nothing changes.
- With a light model, a clean low-risk change costs one cheap review; a
  doubtful one costs a cheap and a full review (the escalation is the price
  of never letting the cheap model decide alone).
- `review = "risky"` trusts the deterministic checks for the change kinds they
  cover; everything else is still read by the reviewer. The rule is a
  conservative table in one place (`joinery/routing.py`), tested per kind.
- The light model must be one the provider lists; Joinery never brokers or
  invents a model, it passes the name through the same routing as the chat.
