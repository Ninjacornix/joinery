"""Puzzle solvability and difficulty for grid games (ADR 0069).

A grid puzzle level (walls, the player, boxes, keys and doors, switches and gates,
goals and exits) is written as text in ``design/puzzles/<name>.toml``
(:mod:`.grid`); a bounded breadth-first search over its states (:mod:`.solve`) says
whether it can be won, how short the shortest win is, and how many of the states a
player can reach are dead ends. The memory-cards starting point's deals are checked
the same way (:mod:`.cards`). :mod:`.check` runs both on a staged change and from
``joinery puzzle check``.
"""
