"""What every agent is told about the game it works on.

One text for every agent (Codex, Claude Code, Gemini CLI, opencode), so the
game brief, the game setup and the art direction steer them all the same way,
and every agent writes those documents in the same plain, concrete way.
"""

from __future__ import annotations

from joinery.bible_writing import AGENT_GUIDE

#: The game: read the brief and setup with the art direction, extend the game
#: through its extension points, and only ever suggest a feature.
GAME_GUIDE = (
    "Read game_brief and game_setup with art_direction; extend the game through "
    "game_setup.extension_points; suggest /feature add, never apply it. "
    "At the start of a session read game_brief, game_setup and art_direction once. "
    "When their suggestions fit what the user is doing, offer one at a time, in one "
    "short line, and never act on a suggestion the user has not asked for. "
    "The user adds or removes features with /feature, and edits the brief with /brief. "
    + AGENT_GUIDE
)

#: How the user decides, in the words the editor shows.
REVIEW_GUIDE = (
    "The user accepts or discards every change in the Review panel. "
    "To approve art direction the user types /art and clicks Approve these rules; "
    "accepting a saved draft alone is not approval."
)
