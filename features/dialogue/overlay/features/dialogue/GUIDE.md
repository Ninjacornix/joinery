# Dialogue

Conversations live in plain-text `.dlg` files, usually under
`res://game/dialogue/`. Edit them like code: one statement a line, blocks
indented with tabs (or spaces). Dialogue text is writing, not an asset.

## The format

```
# A comment (whole lines only).
~ start
Guard: Halt! Who goes there? [ID:guard_halt]
set visits += 1
Guard: You have {{gold}} gold.
if visits > 1
	Guard: Back again?
elif has_cake
	Guard: Is that cake?
else
	Guard: First time here?
- Pay ten gold [if gold >= 10] => paid
- Offer the cake [if has_cake]
	Guard: I love cake.
	set has_cake = false
- Ask about the castle
	Guard: It's old.
	do castle_asked
	=> shrug
- Walk away => END

~ paid
set gold -= 10
do open_gate
Guard: The gate is open.

~ shrug
The guard shrugs\: nothing more to say.
```

| Statement | Meaning |
|---|---|
| `~ name` | A node: where a conversation starts or jumps to. Text before the first `~` is the node `start`. The end of a node ends the conversation. |
| `Speaker: text` | A line. Without `Speaker:` it is narration; write `\:` for a colon in narration. |
| `{{name}}` | The variable's value, in a line or choice. |
| `[ID:key]` | The line's translation key (its gettext context). Optional; add one when the same English needs different translations. |
| `[if condition]` | On a line or choice: shown only when true. |
| `set name = value` | Also `+=`, `-=`, `*=`, `/=`. A variable never set reads as null (and as 0 for `+=`). |
| `do name` | An event the game hears through `Dialogue.event`. |
| `if` / `elif` / `else` | The indented block under the branch that holds runs. |
| `- choice text` | Consecutive choices are shown together; the indented block under the picked one runs, then the conversation goes on after the choices. `- text => node` jumps instead. |
| `=> node`, `=> END` | Jump to a node, or end. |

Conditions and values are Godot expressions over the variables:
`gold >= 10 and not met_guard`, `"knight"`, `visits + 1`. Lines starting with
the lowercase words `set`, `do`, `if`, `elif`, `else` are statements; start
narration with a capital.

## In the game

```gdscript
Dialogue.variables["gold"] = 25
Dialogue.start("res://game/dialogue/guard.dlg")          # node "start"
Dialogue.start("res://game/dialogue/guard.dlg", "paid")  # another node
Dialogue.event.connect(_on_dialogue_event)  # func _on_dialogue_event(name: String)
Dialogue.ended.connect(_on_talk_done)       # func _on_talk_done(source: String)
if Dialogue.is_running():
	return  # in player input code: the player stands still while talking
```

Other calls: `advance()`, `choose(index)`, `stop()`, `current()`,
`current_text()`; signals `started`, `line_shown`, `choices_shown`, `chosen`,
`event`, `ended`. `load("res://game/dialogue/guard.dlg")` gives a
`DialogueFile` (with `errors()` and `node_names()`), so a node can export one:
`@export var dialogue: DialogueFile`.

The box uses the menus' style and follows the Accessibility page: subtitle
size and text size scale it, high contrast turns it black and white, reduced
motion shows lines at once. Accept, Interact or a click advances; arrows and
Accept (or the mouse) pick a choice.

## Translations

With the translations feature, its extract tool reads `.dlg` files: each line
and choice is a message id (English), its `[ID:key]` the context, and each
speaker name a message of its own. Keep English in the file; translators work
in `locale/*.po`.

## Checking a file

`DialogueFile.open(path).errors()` lists problems as `path:line: problem`
(an unknown node, an `elif` without an `if`, a bad expression). The review
shows a changed `.dlg` file as a click-through preview, so the user can play
the conversation before accepting it.

## Playtesting

`Dialogue.playtest_state()` (group `playtest_state`) reports the file, node,
line, nodes visited and variables; the playtester's report includes it.
