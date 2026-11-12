@tool
extends RefCounted
## Reads dialogue text (.dlg files) into a small program that DialogueRunner steps
## through. Plain text, one statement a line, blocks indented with tabs or spaces,
## so a dialogue diffs and merges like code. Close in spirit to Dialogue Manager and
## Yarn Spinner:
##
##   # A comment.
##   ~ start                              a node: a place to start or jump to
##   Guard: Halt! Who goes there?         a line: speaker, colon, text
##   The gate is shut.                    a line nobody says (narration)
##   Guard: Hello, {{name}}. [ID:hello]   {{variable}} is filled in; [ID:key] is the
##                                        line's translation context (msgctxt)
##   Guard: Back again? [if visits > 1]   a line shown only when the condition holds
##   set gold -= 10                       set a variable: =  +=  -=  *=  /=
##   do open_gate                         an event the game hears (Dialogue.event)
##   if gold >= 10                        if / elif / else; the block is indented
##   - Pay ten gold [if gold >= 10] => paid
##                                        a choice: an indented block under it, or a
##                                        jump; [if …] hides it when false
##   => shop                              jump to a node;  => END ends the dialogue
##
## Conditions and values are Godot expressions over the dialogue's variables
## (`gold >= 10 and not met_guard`, `"knight"`, `visits + 1`). A variable never set
## reads as null. Text before the first `~` is the node "start". Falling off the
## end of a node ends the dialogue. `\:` writes a colon in narration.

const END := "END"
const START := "start"
const ASSIGN_OPS: Array[String] = ["=", "+=", "-=", "*=", "/="]
const KEYWORDS: Array[String] = [
	"and", "or", "not", "in", "true", "false", "null", "PI", "TAU", "INF", "NAN", "self"
]
## Statements that may hold an indented block.
const BLOCKS: Array[String] = ["if", "elif", "else", "choice"]


## Parses `text`; `source` names the file in error messages. Returns the program:
##   nodes      {name: index of its first instruction}
##   node_order the node names in file order
##   code       the instructions (each a Dictionary with "op" and "at", its line)
##   texts      every line and choice text: {text, key, speaker, at, kind}
##   errors     "source:line: problem" texts (empty when the file is fine)
static func parse(text: String, source: String = "") -> Dictionary:
	var program: Dictionary = {
		"source": source,
		"nodes": {},
		"node_order": [] as Array[String],
		"code": [] as Array[Dictionary],
		"texts": [] as Array[Dictionary],
		"errors": [] as Array[String],
	}
	var statements: Array[Dictionary] = _statements(text, program)
	var root: Dictionary = _tree(statements, program)
	for node: Dictionary in _nodes(root, program):
		var name: String = node["name"]
		if program["nodes"].has(name):
			_error(program, int(node["at"]), "node %s is defined twice" % name)
			continue
		program["nodes"][name] = (program["code"] as Array).size()
		(program["node_order"] as Array).append(name)
		_compile(node["children"], program)
		_emit(program, {"op": "end", "at": int(node["at"])})
	_resolve_jumps(program)
	return program


## The names an expression reads, in order, without keywords, calls and strings.
static func identifiers(expression: String) -> PackedStringArray:
	var bare: String = _strip_strings(expression)
	var found: PackedStringArray = PackedStringArray()
	var regex: RegEx = RegEx.create_from_string("[A-Za-z_][A-Za-z0-9_]*")
	for match: RegExMatch in regex.search_all(bare):
		var word: String = match.get_string()
		var after: String = bare.substr(match.get_end()).strip_edges()
		var before: String = bare.substr(0, match.get_start()).strip_edges()
		if word in KEYWORDS or after.begins_with("(") or before.ends_with("."):
			continue
		if not found.has(word):
			found.append(word)
	return found


## Whether `expression` parses as a Godot expression over its own names.
static func expression_error(expression: String) -> String:
	var parser: Expression = Expression.new()
	if parser.parse(expression, identifiers(expression)) != OK:
		return parser.get_error_text()
	return ""


# --- Lines to statements ---------------------------------------------------------


static func _statements(text: String, program: Dictionary) -> Array[Dictionary]:
	var found: Array[Dictionary] = []
	var lines: PackedStringArray = text.replace("\r\n", "\n").split("\n")
	for index: int in lines.size():
		var raw: String = lines[index]
		var body: String = raw.strip_edges()
		if body.is_empty() or body.begins_with("#"):
			continue
		var statement: Dictionary = _statement(body, index + 1, program)
		if statement.is_empty():
			continue
		statement["indent"] = _indent(raw)
		statement["at"] = index + 1
		statement["children"] = [] as Array[Dictionary]
		found.append(statement)
	return found


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


static func _statement(body: String, at: int, program: Dictionary) -> Dictionary:
	if body.begins_with("~"):
		var name: String = body.substr(1).strip_edges()
		if not _is_name(name):
			_error(program, at, "a node name is letters, digits, _ or -: '%s'" % name)
			return {}
		return {"kind": "node", "name": name}
	if body.begins_with("=>"):
		var target: String = body.substr(2).strip_edges()
		if not _is_name(target):
			_error(program, at, "=> needs a node name or END")
			return {}
		return {"kind": "jump", "target": target}
	if body == "-" or body.begins_with("- "):
		return _choice(body.substr(1).strip_edges(), at, program)
	var word: String = body.get_slice(" ", 0).trim_suffix(":")
	match word:
		"set":
			return _set_statement(body.substr(4).strip_edges(), at, program)
		"do":
			var event: String = body.substr(3).strip_edges()
			if not event.is_valid_identifier():
				_error(program, at, "do needs an event name such as open_gate")
				return {}
			return {"kind": "do", "name": event}
		"if", "elif":
			var condition: String = body.substr(word.length()).strip_edges().trim_suffix(":")
			if condition.strip_edges().is_empty():
				_error(program, at, "%s needs a condition" % word)
				return {}
			_check_expression(condition, at, program)
			return {"kind": word, "cond": condition.strip_edges()}
		"else":
			if body.trim_suffix(":").strip_edges() != "else":
				_error(program, at, "else takes no condition (use elif)")
			return {"kind": "else"}
	return _line(body, at, program)


static func _set_statement(rest: String, at: int, program: Dictionary) -> Dictionary:
	var regex: RegEx = RegEx.create_from_string(
		"^([A-Za-z_][A-Za-z0-9_]*)\\s*(\\+=|-=|\\*=|/=|=)\\s*(.+)$"
	)
	var match: RegExMatch = regex.search(rest)
	if match == null:
		_error(program, at, "set reads: set name = value (or += -= *= /=)")
		return {}
	var value: String = match.get_string(3).strip_edges()
	_check_expression(value, at, program)
	return {
		"kind": "set", "name": match.get_string(1), "assign": match.get_string(2), "expr": value
	}


static func _choice(rest: String, at: int, program: Dictionary) -> Dictionary:
	var target: String = ""
	var arrow: int = rest.rfind("=>")
	if arrow >= 0:
		target = rest.substr(arrow + 2).strip_edges()
		rest = rest.substr(0, arrow).strip_edges()
		if not _is_name(target):
			_error(program, at, "=> needs a node name or END")
			target = ""
	var tagged: Dictionary = _tags(rest, at, program)
	if str(tagged["text"]).is_empty():
		_error(program, at, "a choice needs text")
	var statement: Dictionary = {
		"kind": "choice",
		"text": tagged["text"],
		"key": tagged["key"],
		"cond": tagged["cond"],
		"target": target,
	}
	_text(program, statement, "", at, "choice")
	return statement


static func _line(body: String, at: int, program: Dictionary) -> Dictionary:
	var tagged: Dictionary = _tags(body, at, program)
	var text: String = tagged["text"]
	var speaker: String = ""
	var colon: int = _speaker_colon(text)
	if colon > 0:
		speaker = text.substr(0, colon).strip_edges()
		text = text.substr(colon + 1).strip_edges()
	text = text.replace("\\:", ":")
	if text.is_empty():
		_error(program, at, "a line needs text after the speaker")
		return {}
	var statement: Dictionary = {
		"kind": "line",
		"speaker": speaker,
		"text": text,
		"key": tagged["key"],
		"cond": tagged["cond"]
	}
	_text(program, statement, speaker, at, "line")
	return statement


## The colon after a speaker name: a short name, no brackets or braces, not escaped.
static func _speaker_colon(text: String) -> int:
	var colon: int = text.find(":")
	if colon <= 0 or colon > 40 or text[colon - 1] == "\\":
		return -1
	var name: String = text.substr(0, colon)
	for bad: String in ["[", "]", "{", "}", '"', "="]:
		if name.contains(bad):
			return -1
	if colon + 1 < text.length() and text[colon + 1] != " " and text[colon + 1] != "\t":
		return -1
	return colon


## Takes [if …] and [ID:…] out of `body`.
static func _tags(body: String, at: int, program: Dictionary) -> Dictionary:
	var result: Dictionary = {"text": body, "key": "", "cond": ""}
	var key_regex: RegEx = RegEx.create_from_string("\\[ID:([^\\]]*)\\]")
	var key: RegExMatch = key_regex.search(body)
	if key != null:
		result["key"] = key.get_string(1).strip_edges()
		body = body.replace(key.get_string(), "")
	var cond_regex: RegEx = RegEx.create_from_string("\\[if\\s+([^\\]]+)\\]")
	var cond: RegExMatch = cond_regex.search(body)
	if cond != null:
		result["cond"] = cond.get_string(1).strip_edges()
		_check_expression(str(result["cond"]), at, program)
		body = body.replace(cond.get_string(), "")
	result["text"] = body.strip_edges()
	return result


static func _text(
	program: Dictionary, statement: Dictionary, speaker: String, at: int, kind: String
) -> void:
	(
		(program["texts"] as Array)
		. append(
			{
				"text": statement["text"],
				"key": statement["key"],
				"speaker": speaker,
				"at": at,
				"kind": kind,
			}
		)
	)


static func _check_expression(expression: String, at: int, program: Dictionary) -> void:
	var problem: String = expression_error(expression)
	if not problem.is_empty():
		_error(program, at, "'%s' is not an expression: %s" % [expression, problem])


# --- Statements to a tree --------------------------------------------------------


static func _tree(statements: Array[Dictionary], program: Dictionary) -> Dictionary:
	var root: Dictionary = {"kind": "root", "children": [] as Array[Dictionary]}
	var stack: Array[Dictionary] = [{"indent": -1, "item": root}]
	for statement: Dictionary in statements:
		var indent: int = statement["indent"]
		while int(stack.back()["indent"]) >= indent:
			stack.pop_back()
		var parent: Dictionary = stack.back()["item"]
		if parent["kind"] != "root" and not str(parent["kind"]) in BLOCKS:
			_error(
				program,
				int(statement["at"]),
				"only if, elif, else and choices hold an indented block"
			)
		if statement["kind"] == "node" and parent["kind"] != "root":
			_error(program, int(statement["at"]), "a node (~) starts at the left edge")
		(parent["children"] as Array).append(statement)
		stack.append({"indent": indent, "item": statement})
	return root


## Splits the top level into nodes. Text before the first `~` is the node "start".
static func _nodes(root: Dictionary, program: Dictionary) -> Array[Dictionary]:
	var nodes: Array[Dictionary] = []
	var current: Dictionary = {"name": START, "at": 1, "children": [] as Array[Dictionary]}
	var named: bool = false
	for item: Dictionary in root["children"]:
		if item["kind"] != "node":
			(current["children"] as Array).append(item)
			continue
		if named or not (current["children"] as Array).is_empty():
			nodes.append(current)
		current = {"name": item["name"], "at": item["at"], "children": [] as Array[Dictionary]}
		named = true
		if not (item["children"] as Array).is_empty():
			_error(program, int(item["at"]), "indent the node's lines no deeper than the ~")
	if named or not (current["children"] as Array).is_empty():
		nodes.append(current)
	return nodes


# --- Tree to code ----------------------------------------------------------------


static func _compile(items: Array, program: Dictionary) -> void:
	var index: int = 0
	while index < items.size():
		var item: Dictionary = items[index]
		var at: int = item["at"]
		match str(item["kind"]):
			"line":
				_emit(
					program,
					{
						"op": "line",
						"speaker": item["speaker"],
						"text": item["text"],
						"key": item["key"],
						"cond": item["cond"],
						"at": at,
					}
				)
			"set":
				_emit(
					program,
					{
						"op": "set",
						"name": item["name"],
						"assign": item["assign"],
						"expr": item["expr"],
						"at": at
					}
				)
			"do":
				_emit(program, {"op": "do", "name": item["name"], "at": at})
			"jump":
				_emit(program, {"op": "jump", "node": item["target"], "pc": -1, "at": at})
			"if":
				index = _compile_if(items, index, program)
				continue
			"elif", "else":
				_error(program, at, "%s without an if above it" % item["kind"])
			"choice":
				index = _compile_choices(items, index, program)
				continue
			"node":
				pass
		index += 1


static func _compile_if(items: Array, index: int, program: Dictionary) -> int:
	var ends: Array[int] = []
	var first: bool = true
	while index < items.size():
		var item: Dictionary = items[index]
		var kind: String = item["kind"]
		if (first and kind != "if") or (not first and kind != "elif" and kind != "else"):
			break
		first = false
		index += 1
		if kind == "else":
			_compile(item["children"], program)
			break
		var branch: int = _emit(
			program, {"op": "branch", "cond": item["cond"], "else_pc": -1, "at": item["at"]}
		)
		_compile(item["children"], program)
		ends.append(_emit(program, {"op": "goto", "pc": -1, "at": item["at"]}))
		_code(program)[branch]["else_pc"] = _code(program).size()
	for end: int in ends:
		_code(program)[end]["pc"] = _code(program).size()
	return index


static func _compile_choices(items: Array, index: int, program: Dictionary) -> int:
	var options: Array[Dictionary] = []
	var choice: int = _emit(
		program, {"op": "choice", "options": options, "after": -1, "at": items[index]["at"]}
	)
	var ends: Array[int] = []
	while index < items.size() and str(items[index]["kind"]) == "choice":
		var item: Dictionary = items[index]
		(
			options
			. append(
				{
					"text": item["text"],
					"key": item["key"],
					"cond": item["cond"],
					"pc": _code(program).size(),
					"at": item["at"],
				}
			)
		)
		_compile(item["children"], program)
		if not str(item["target"]).is_empty():
			_emit(program, {"op": "jump", "node": item["target"], "pc": -1, "at": item["at"]})
		else:
			ends.append(_emit(program, {"op": "goto", "pc": -1, "at": item["at"]}))
		index += 1
	var after: int = _code(program).size()
	_code(program)[choice]["after"] = after
	for end: int in ends:
		_code(program)[end]["pc"] = after
	return index


static func _resolve_jumps(program: Dictionary) -> void:
	var nodes: Dictionary = program["nodes"]
	for instruction: Dictionary in _code(program):
		if instruction["op"] != "jump":
			continue
		var target: String = instruction["node"]
		if target == END:
			instruction["pc"] = -1
		elif nodes.has(target):
			instruction["pc"] = int(nodes[target])
		else:
			_error(
				program, int(instruction["at"]), "=> %s: there is no node ~ %s" % [target, target]
			)


static func _emit(program: Dictionary, instruction: Dictionary) -> int:
	_code(program).append(instruction)
	return _code(program).size() - 1


static func _code(program: Dictionary) -> Array:
	return program["code"]


static func _error(program: Dictionary, at: int, message: String) -> void:
	var source: String = str(program["source"])
	var where: String = "%s:%d" % [source, at] if not source.is_empty() else "line %d" % at
	(program["errors"] as Array).append("%s: %s" % [where, message])


static func _is_name(name: String) -> bool:
	if name.is_empty():
		return false
	for character: String in name:
		if not (
			character == "_"
			or character == "-"
			or character.is_valid_identifier()
			or character.is_valid_int()
		):
			return false
	return true


static func _strip_strings(expression: String) -> String:
	var regex: RegEx = RegEx.create_from_string("\"(?:[^\"\\\\]|\\\\.)*\"|'(?:[^'\\\\]|\\\\.)*'")
	return regex.sub(expression, '""', true)
