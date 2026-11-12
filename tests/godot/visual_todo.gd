extends RefCounted
## The visual suite's to-do screens (ADR 0060): a request's steps and "done when" items waiting
## for Go, then the items checked. [param suite] is visual_sidebar_test.gd, whose `_shot` these
## use; the sidebar's review shows the same results (visual_review.gd).

const STEPS := [
	"Put a lever beside the vault gate",
	"Open the gate when the lever is pulled",
	"Put the key on the vault's table",
]
const DONE := [
	{"id": "d1", "text": "The player can reach the vault", "kind": "reach"},
	{"id": "d2", "text": "The gate opens when the lever is pulled", "kind": "test"},
	{"id": "d3", "text": "No errors in 60 s of play", "kind": "no_errors"},
]


## Results as the host sends them: one of each.
static func results() -> Array:
	return [
		{
			"id": "d1",
			"text": "The player can reach the vault",
			"status": "pass",
			"evidence": "The bot reached Vault at 0:42, seed 4242.",
		},
		{
			"id": "d2",
			"text": "The gate opens when the lever is pulled",
			"status": "fail",
			"evidence": "res://tests/test_gate.gd isn't in the change.",
		},
		{
			"id": "d3",
			"text": "No errors in 60 s of play",
			"status": "inconclusive",
			"evidence": "The game ran only 0:41 of the 60 s asked for.",
		},
	]


static func shots(suite: Variant, light: bool, width: int, chat: Control) -> void:
	var tasks: Node = chat.get("_tasks")
	var todo: Node = tasks.get("todo")
	var event := {
		"kind": "todo", "todo_id": "t1", "state": "waiting", "steps": STEPS, "done_when": DONE
	}
	todo.call("on_event", event)
	await suite._shot("todo", light, width, chat)
	event["state"] = "going"
	todo.call("on_event", event)
	(
		todo
		. call(
			"on_event",
			{
				"kind": "todo",
				"todo_id": "t1",
				"state": "checked",
				"results": results(),
				"summary": "1 of 3 done",
			}
		)
	)
	await suite._shot("todo-checked", light, width, chat)
	var card: Control = tasks.get("todo_card")
	card.free()
	tasks.set("todo_card", null)
	tasks.hide()
