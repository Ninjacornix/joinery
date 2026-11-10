# Bible approved at creation

Accepted: 2026-11-10.

## Context

Art and audio work waits for an approved `art/bible.md` (`joinery.art_approval`:
the approval is bound to the bible's SHA-256 and names who approved it). A new
project's bible is drafted from the answers the user just typed in the New
project wizard, yet the first art request still stopped at the art gate and
asked the user to approve those same words with `/art`. That extra step
approved nothing the user had not already said.

## Decision

A bible drafted from the user's own answers when the project is made counts as
approved by the user at creation. Right after `joinery game apply` (the wizard,
and `joinery new --blueprint`) or `joinery bible` renders the answers file,
project setup records the same approval the Approve button records, with
`approved_by = "user (at project creation)"`. Skipped questions do not stop
it: their sections stay open to fill in later, and a bible drafted from the
blueprint's preset alone is approved the same way (the user's decision,
2026-11-10). Only creation accepts open sections; the Approve button still
needs them filled. A project with no bible still needs the user's Approve.

The approval stays bound to the bible's bytes. Any later change, the user's own
edit or an accepted change, lapses it, and the user approves again with one
click in `/art`. Approving remains a user act only: it happens in the
command-line setup path at creation, never through a model tool. The gateway
RPC and the registry still have no approval operation.

## Consequences

- Art and audio work can start in a fresh project without a separate review.
- The approval record tells creation-time approval apart from a later click.
- Agents gain nothing: they cannot approve, and any bible change they make
  through an accepted change lapses the approval as before.
