@tool
extends RefCounted
## A small behaviour-tree runtime: the node kinds, and the reader that builds a
## tree from its text. One tree is a few lines an agent or a person edits:
##
##   # Guard: hits when close, chases what it sees, else patrols.
##   selector
##   	sequence
##   		condition target_near
##   		cooldown 1.5
##   			action attack
##   	sequence
##   		condition sees_target
##   		action chase
##   	action patrol
##
## Kinds:
##   sequence          runs children in order; fails at the first that fails
##   selector          tries children in order; succeeds at the first that succeeds
##   invert            one child; success and failure swap
##   cooldown <s>      one child; after it succeeds, fails for <s> seconds
##   condition <name>  asks the agent (built-in checks or the body's bt_<name>())
##   action <name>     does something (built-in moves or the body's bt_<name>())
##   wait <s>          running for <s> seconds, then success
## Arguments follow as name=value (`action chase speed=200`); a bare first value is
## the argument `value` (`cooldown 1.5`, `condition health_below 0.3`).
## Sequence and selector start from their first child every tick, so a higher
## branch takes over as soon as its condition holds; the child that was running
## and is no longer reached is interrupted.

enum Status { SUCCESS, FAILURE, RUNNING }

const SUCCESS := Status.SUCCESS
const FAILURE := Status.FAILURE
const RUNNING := Status.RUNNING
const NAMES := {SUCCESS: "success", FAILURE: "failure", RUNNING: "running"}
const KINDS: Array[String] = [
	"sequence", "selector", "invert", "cooldown", "condition", "action", "wait"
]
const ONE_CHILD: Array[String] = ["invert", "cooldown"]
const LEAVES: Array[String] = ["condition", "action", "wait"]


## One node of a tree. `tick(agent)` returns SUCCESS, FAILURE or RUNNING. `agent` is
## the BehaviourAgent running the tree: it answers conditions and actions, and
## gives the time.
class BTNode:
	extends RefCounted
	var kind: String = ""
	var name: String = ""
	var args: Dictionary = {}
	var children: Array[BTNode] = []
	## The last status, or -1 before the first tick.
	var status: int = -1
	## The line of the tree text this node came from.
	var at: int = 0

	func tick(_agent: Object) -> int:
		return FAILURE

	## Called when a running node is no longer reached.
	func interrupt(agent: Object) -> void:
		if status == RUNNING:
			for child: BTNode in children:
				child.interrupt(agent)
			status = -1

	func label() -> String:
		var text: String = kind if name.is_empty() else "%s %s" % [kind, name]
		for key: String in args:
			text += (
				" %s=%s" % [key, var_to_str(args[key])] if key != "value" else " %s" % [args[key]]
			)
		return text

	func _done(result: int) -> int:
		status = result
		return result


class Sequence:
	extends BTNode

	func tick(agent: Object) -> int:
		for index: int in children.size():
			var result: int = children[index].tick(agent)
			if result != SUCCESS:
				_interrupt_after(agent, index)
				return _done(result)
		return _done(SUCCESS)

	func _interrupt_after(agent: Object, index: int) -> void:
		for later: int in range(index + 1, children.size()):
			children[later].interrupt(agent)


class Selector:
	extends BTNode

	func tick(agent: Object) -> int:
		for index: int in children.size():
			var result: int = children[index].tick(agent)
			if result != FAILURE:
				for later: int in range(index + 1, children.size()):
					children[later].interrupt(agent)
				return _done(result)
		return _done(FAILURE)


class Invert:
	extends BTNode

	func tick(agent: Object) -> int:
		var result: int = children[0].tick(agent)
		if result == RUNNING:
			return _done(RUNNING)
		return _done(FAILURE if result == SUCCESS else SUCCESS)


class Cooldown:
	extends BTNode
	## The agent's clock time when the child may run again.
	var ready_at: float = -INF

	func tick(agent: Object) -> int:
		var now: float = agent.call(&"clock")
		if now < ready_at:
			return _done(FAILURE)
		var result: int = children[0].tick(agent)
		if result == SUCCESS:
			ready_at = now + float(args.get("value", 1.0))
		return _done(result)

	func left(agent: Object) -> float:
		return maxf(ready_at - float(agent.call(&"clock")), 0.0)


class Wait:
	extends BTNode
	var until: float = -1.0

	func tick(agent: Object) -> int:
		var now: float = agent.call(&"clock")
		if status != RUNNING:
			until = now + float(args.get("value", 1.0))
		return _done(RUNNING if now < until else SUCCESS)


class Condition:
	extends BTNode

	func tick(agent: Object) -> int:
		var holds: bool = agent.call(&"check", name, args)
		return _done(SUCCESS if holds else FAILURE)


class Action:
	extends BTNode

	func tick(agent: Object) -> int:
		var first: bool = status != RUNNING
		return _done(int(agent.call(&"act", name, args, first, self)))

	func interrupt(agent: Object) -> void:
		if status == RUNNING:
			agent.call(&"stop_action", name, self)
		super.interrupt(agent)


## Reads tree text. Returns {"root": BTNode or null, "errors": ["line N: …"]}.
static func parse(text: String) -> Dictionary:
	var errors: Array[String] = []
	var stack: Array[Dictionary] = []
	var root: BTNode = null
	var lines: PackedStringArray = text.replace("\r\n", "\n").split("\n")
	for index: int in lines.size():
		var raw: String = lines[index]
		var body: String = raw.strip_edges()
		if body.is_empty() or body.begins_with("#"):
			continue
		var at: int = index + 1
		var node: BTNode = _node(body, at, errors)
		if node == null:
			continue
		var indent: int = _indent(raw)
		while not stack.is_empty() and int(stack.back()["indent"]) >= indent:
			stack.pop_back()
		if stack.is_empty():
			if root != null:
				errors.append("line %d: a tree has one root; indent this under it" % at)
				continue
			root = node
		else:
			var parent: BTNode = stack.back()["node"]
			if parent.kind in LEAVES:
				errors.append("line %d: %s holds no children" % [at, parent.kind])
				continue
			if parent.kind in ONE_CHILD and not parent.children.is_empty():
				errors.append("line %d: %s holds one child" % [at, parent.kind])
				continue
			parent.children.append(node)
		stack.append({"indent": indent, "node": node})
	if root == null and errors.is_empty():
		errors.append("line 1: the tree is empty")
	_check_children(root, errors)
	return {"root": root if errors.is_empty() else null, "errors": errors}


## The tree as indented text with each node's last status, for people and the
## playtester: "selector [running]\n  sequence [failure]\n…".
static func describe(root: BTNode) -> String:
	var lines: PackedStringArray = PackedStringArray()
	_describe(root, 0, lines)
	return "\n".join(lines)


## The chain of running nodes from the root: "selector > sequence > action chase".
static func running_path(root: BTNode) -> String:
	var parts: PackedStringArray = PackedStringArray()
	var node: BTNode = root
	while node != null and node.status == RUNNING:
		parts.append(node.label())
		var next: BTNode = null
		for child: BTNode in node.children:
			if child.status == RUNNING:
				next = child
				break
		node = next
	return " > ".join(parts)


static func status_name(status: int) -> String:
	return str(NAMES.get(status, "not run"))


static func _describe(node: BTNode, depth: int, lines: PackedStringArray) -> void:
	if node == null:
		return
	lines.append("%s%s [%s]" % ["  ".repeat(depth), node.label(), status_name(node.status)])
	for child: BTNode in node.children:
		_describe(child, depth + 1, lines)


static func _node(body: String, at: int, errors: Array[String]) -> BTNode:
	var words: PackedStringArray = body.split(" ", false)
	var kind: String = words[0]
	if not kind in KINDS:
		errors.append("line %d: '%s' is not a node kind (%s)" % [at, kind, ", ".join(KINDS)])
		return null
	var node: BTNode = _make(kind)
	node.kind = kind
	node.at = at
	var rest: int = 1
	if kind == "condition" or kind == "action":
		if words.size() < 2 or not words[1].is_valid_identifier():
			errors.append("line %d: %s needs a name, such as %s" % [at, kind, _example(kind)])
			return null
		node.name = words[1]
		rest = 2
	for index: int in range(rest, words.size()):
		var word: String = words[index]
		if word.contains("="):
			node.args[word.get_slice("=", 0)] = _value(word.substr(word.find("=") + 1))
		elif index == rest:
			node.args["value"] = _value(word)
		else:
			errors.append("line %d: '%s' should be name=value" % [at, word])
	if (
		kind in ["cooldown", "wait"]
		and not (node.args.get("value") is float or node.args.get("value") is int)
	):
		errors.append("line %d: %s needs seconds, such as %s 1.5" % [at, kind, kind])
	return node


static func _make(kind: String) -> BTNode:
	match kind:
		"sequence":
			return Sequence.new()
		"selector":
			return Selector.new()
		"invert":
			return Invert.new()
		"cooldown":
			return Cooldown.new()
		"wait":
			return Wait.new()
		"condition":
			return Condition.new()
	return Action.new()


static func _check_children(node: BTNode, errors: Array[String]) -> void:
	if node == null:
		return
	if not node.kind in LEAVES and node.children.is_empty():
		errors.append("line %d: %s needs children indented under it" % [node.at, node.kind])
	for child: BTNode in node.children:
		_check_children(child, errors)


static func _value(text: String) -> Variant:
	if text.is_valid_int():
		return text.to_int()
	if text.is_valid_float():
		return text.to_float()
	if text == "true" or text == "false":
		return text == "true"
	return text.trim_prefix('"').trim_suffix('"')


static func _indent(raw: String) -> int:
	var width: int = 0
	for character: String in raw:
		if character == "\t":
			width += 4
		elif character == " ":
			width += 1
		else:
			break
	return width


static func _example(kind: String) -> String:
	return "sees_target" if kind == "condition" else "chase"
