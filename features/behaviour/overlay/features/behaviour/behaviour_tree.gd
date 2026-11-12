@tool
class_name BehaviourTree
extends Resource
## A behaviour tree saved as a .tres whose `source` is the tree's text, so the file
## diffs line by line and an agent edits it like code (see bt_nodes.gd for the
## format). A BehaviourAgent builds a fresh tree from it for each enemy.
##
##   [gd_resource type="Resource" script_class="BehaviourTree" format=3]
##   [ext_resource type="Script" path="res://features/behaviour/behaviour_tree.gd" id="1"]
##   [resource]
##   script = ExtResource("1")
##   source = "selector
##   	sequence
##   		condition target_near
##   		action attack
##   	action chase
##   "

const Nodes := preload("res://features/behaviour/bt_nodes.gd")

@export_multiline var source: String = ""


static func from_text(text: String) -> BehaviourTree:
	var tree: BehaviourTree = BehaviourTree.new()
	tree.source = text
	return tree


## A new tree of nodes to run (each agent needs its own), or null with errors.
func build() -> Nodes.BTNode:
	return Nodes.parse(source)["root"]


## Problems in the text, as "line N: …" (empty when it is fine).
func errors() -> Array[String]:
	var found: Array[String] = []
	found.assign(Nodes.parse(source)["errors"])
	return found
