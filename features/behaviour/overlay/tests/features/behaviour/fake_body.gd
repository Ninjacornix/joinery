extends Node2D
## A body for the behaviour checks: conditions and actions the tree calls by name.

const BTNodes := preload("res://features/behaviour/bt_nodes.gd")

## Answers bt_ready: the condition the checks flip.
var ready_flag: bool = false
## Names of the actions run, in order.
var calls: Array[String] = []


func bt_ready(_agent: Node, _args: Dictionary) -> bool:
	return ready_flag


func bt_count(_agent: Node, args: Dictionary) -> int:
	calls.append("count:%s" % str(args.get("tag", "")))
	return BTNodes.SUCCESS


func bt_busy(_agent: Node, _args: Dictionary) -> int:
	calls.append("busy")
	return BTNodes.RUNNING


func bt_fail(_agent: Node, _args: Dictionary) -> int:
	calls.append("fail")
	return BTNodes.FAILURE
