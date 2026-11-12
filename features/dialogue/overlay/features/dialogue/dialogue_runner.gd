@tool
extends RefCounted
## Steps through a parsed dialogue (dialogue_parser.gd): runs `set`, `do`, `if` and
## jumps, and stops at the next thing to show: a line, a set of choices, or the end.
## The Dialogue autoload drives one in play; the review's click-through preview
## drives one in the editor, so both read a file the same way.
##
##   var runner := DialogueRunner.new(program, variables)
##   var step := runner.start("start")   # {"type": "line", "speaker", "text", ...}
##   step = runner.advance()             # after a line
##   step = runner.choose(0)             # after choices (the option's "index")
##
## A step is one of:
##   {"type": "line", "speaker", "text", "key", "at", "node"}
##   {"type": "choices", "options": [{"index", "text", "key", "at"}], "at", "node"}
##   {"type": "end", "node"}
## Texts are the file's English: show them through text_of() and speaker_of(),
## which translate and fill in {{variables}}.

signal event(name: String)

const Parser := preload("res://features/dialogue/dialogue_parser.gd")
## Instructions run without showing anything before the runner gives up (a loop).
const MAX_STEPS := 10000

var program: Dictionary
## The dialogue's variables; shared with whoever passed it in.
var variables: Dictionary
## The node the runner is in, and the next instruction (-1 when ended).
var node: String = ""
var pc: int = -1
var step: Dictionary = {"type": "end", "node": ""}
## Problems met while running (bad expressions, a loop), as "source:line: …".
var problems: Array[String] = []
## Node names visited, in order (the playtester and the preview show it).
var trail: Array[String] = []


func _init(parsed: Dictionary, shared_variables: Dictionary = {}) -> void:
	program = parsed
	variables = shared_variables


func start(node_name: String = Parser.START) -> Dictionary:
	var nodes: Dictionary = program.get("nodes", {})
	if not nodes.has(node_name):
		_problem(0, "there is no node ~ %s" % node_name)
		return _finish()
	node = node_name
	trail = [node_name]
	pc = int(nodes[node_name])
	return _run()


## Continues after a line. Does nothing while choices wait for an answer.
func advance() -> Dictionary:
	if step.get("type") == "line":
		return _run()
	return step


## Picks the option whose "index" is `index` from the choices shown.
func choose(index: int) -> Dictionary:
	if step.get("type") != "choices":
		return step
	var instruction: Dictionary = _code()[int(step["pc"])]
	var options: Array = instruction["options"]
	if index < 0 or index >= options.size():
		return step
	pc = int(options[index]["pc"])
	return _run()


func is_running() -> bool:
	return step.get("type") != "end"


## A text of a step, translated (its [ID:…] key is the context) and filled in.
func text_of(entry: Dictionary) -> String:
	var text: String = str(entry.get("text", ""))
	var key: String = str(entry.get("key", ""))
	return fill(TranslationServer.translate(text, key))


func speaker_of(entry: Dictionary) -> String:
	var speaker: String = str(entry.get("speaker", ""))
	return "" if speaker.is_empty() else fill(TranslationServer.translate(speaker))


## Replaces {{name}} with the variable's value.
func fill(text: String) -> String:
	if not text.contains("{{"):
		return text
	var regex: RegEx = RegEx.create_from_string("\\{\\{\\s*([A-Za-z_][A-Za-z0-9_]*)\\s*\\}\\}")
	var out: String = text
	for found: RegExMatch in regex.search_all(text):
		var value: Variant = variables.get(found.get_string(1))
		out = out.replace(found.get_string(), "" if value == null else str(value))
	return out


## Evaluates an expression over the variables. A failure is a problem and null.
func evaluate(expression: String, at: int = 0) -> Variant:
	var names: PackedStringArray = Parser.identifiers(expression)
	var values: Array = []
	for name: String in names:
		values.append(variables.get(name))
	var parser: Expression = Expression.new()
	if parser.parse(expression, names) != OK:
		_problem(at, "'%s': %s" % [expression, parser.get_error_text()])
		return null
	var result: Variant = parser.execute(values, null, false, true)
	if parser.has_execute_failed():
		_problem(at, "'%s' could not be worked out: %s" % [expression, parser.get_error_text()])
		return null
	return result


func test(condition: String, at: int = 0) -> bool:
	if condition.strip_edges().is_empty():
		return true
	return truthy(evaluate(condition, at))


## How a condition's value reads: null, false, 0 and "" are false.
static func truthy(value: Variant) -> bool:
	match typeof(value):
		TYPE_NIL:
			return false
		TYPE_BOOL:
			return value
		TYPE_INT, TYPE_FLOAT:
			return value != 0
		TYPE_STRING, TYPE_STRING_NAME:
			return not str(value).is_empty()
	return true


## Where the runner is, for the playtester, bug reports and the preview.
func state() -> Dictionary:
	return {
		"source": str(program.get("source", "")),
		"node": node,
		"line": int(step.get("at", 0)),
		"showing": str(step.get("type", "end")),
		"trail": trail.duplicate(),
		"variables": variables.duplicate(true),
		"problems": problems.duplicate(),
	}


func _run() -> Dictionary:
	var code: Array = _code()
	var budget: int = MAX_STEPS
	while budget > 0:
		budget -= 1
		if pc < 0 or pc >= code.size():
			return _finish()
		var instruction: Dictionary = code[pc]
		var at: int = int(instruction.get("at", 0))
		match str(instruction["op"]):
			"line":
				pc += 1
				if test(str(instruction["cond"]), at):
					step = {
						"type": "line",
						"speaker": instruction["speaker"],
						"text": instruction["text"],
						"key": instruction["key"],
						"at": at,
						"node": node,
					}
					return step
			"choice":
				var shown: Array[Dictionary] = []
				var options: Array = instruction["options"]
				for index: int in options.size():
					var option: Dictionary = options[index]
					if test(str(option["cond"]), int(option["at"])):
						(
							shown
							. append(
								{
									"index": index,
									"text": option["text"],
									"key": option["key"],
									"at": option["at"],
								}
							)
						)
				if shown.is_empty():
					pc = int(instruction["after"])
					continue
				step = {"type": "choices", "options": shown, "at": at, "node": node, "pc": pc}
				return step
			"set":
				_assign(instruction, at)
				pc += 1
			"do":
				pc += 1
				event.emit(str(instruction["name"]))
			"branch":
				pc = pc + 1 if test(str(instruction["cond"]), at) else int(instruction["else_pc"])
			"goto":
				pc = int(instruction["pc"])
			"jump":
				pc = int(instruction["pc"])
				if pc >= 0:
					node = str(instruction["node"])
					trail.append(node)
			"end":
				return _finish()
	_problem(0, "ran %d steps without showing anything; is there a loop?" % MAX_STEPS)
	return _finish()


func _assign(instruction: Dictionary, at: int) -> void:
	var name: String = instruction["name"]
	var value: Variant = evaluate(str(instruction["expr"]), at)
	var assign: String = instruction["assign"]
	if assign == "=":
		variables[name] = value
		return
	var current: Variant = variables.get(name)
	if current == null:
		current = 0
	if not (current is int or current is float) or not (value is int or value is float):
		_problem(at, "%s %s needs numbers" % [name, assign])
		return
	match assign:
		"+=":
			variables[name] = current + value
		"-=":
			variables[name] = current - value
		"*=":
			variables[name] = current * value
		"/=":
			if value == 0:
				_problem(at, "%s /= 0" % name)
			else:
				variables[name] = current / value


func _finish() -> Dictionary:
	pc = -1
	step = {"type": "end", "node": node}
	return step


func _code() -> Array:
	return program.get("code", [])


func _problem(at: int, message: String) -> void:
	var source: String = str(program.get("source", ""))
	var where: String = "%s:%d" % [source, at] if not source.is_empty() else "line %d" % at
	problems.append("%s: %s" % [where, message])
	push_warning("Dialogue %s: %s" % [where, message])
