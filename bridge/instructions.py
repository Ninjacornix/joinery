"""What every agent is told about the game it works on.

One text for every agent (Codex, Claude Code, Gemini CLI, opencode), so the
game brief, the game setup and the art direction steer them all the same way,
and every agent writes those documents in the same plain, concrete way.
"""

from __future__ import annotations

from joinery.bible_writing import AGENT_GUIDE
from joinery.chat_attachments import NOTE as ATTACHMENT_NOTE

#: Objects move as one piece. A chair built from loose boxes in the level selects
#: a box, not the chair.
OBJECT_GUIDE = (
    "Build every object the player would think of as one thing (a chair, a table, a TV, a door) "
    "as one unit: a Blender model when art work is allowed, otherwise its own scene "
    "(scenes/props/<name>.tscn) whose root is the object, with its parts as children. "
    "Place it in the level as an instance of that scene or model, once per copy, so clicking "
    "any part selects the whole object and it moves as one. Never assemble an object from "
    "loose meshes directly in the level. "
    "The scene on screen (editor_context.scene_path), a scene in "
    "editor_context.unsaved_scenes (open with edits not saved yet) and a new scene with "
    "no saved file change live with edit_scene, so the user sees the change in the "
    "viewport. Every other scene changes as a staged file through the Godot tools; "
    "those tools refuse the live ones. Never change one scene both ways in one turn. "
    "Stage a model or texture and the scene that uses it in one change: a scene that "
    "points at a file neither the project nor the same change has is refused, and a "
    "scene accepted without its model opens with a missing file. "
)

#: The game: read the brief and setup with the art direction, extend the game
#: through its extension points, and only ever suggest a feature.
GAME_GUIDE = (
    "Read game_brief and game_setup with art_direction; extend the game through "
    "game_setup.extension_points; suggest /feature add, never apply it. "
    "At the start of a session read game_brief, game_setup and art_direction once. "
    "When their suggestions fit what the user is doing, offer one at a time, in one "
    "short line, and never act on a suggestion the user has not asked for. "
    "The user adds or removes features with /feature, and edits the brief with /brief. "
    "When the user asks you to look at something in the editor, call view_editor to see "
    "the viewport as they see it. "
    + ATTACHMENT_NOTE
    + " "
    + OBJECT_GUIDE
    + AGENT_GUIDE
)

#: How the user decides, in the words the editor shows.
REVIEW_GUIDE = (
    "The user accepts or discards every change in the Review panel. "
    "When your change is complete, call submit_for_review with one short line naming "
    "what changed. Joinery checks it (art bible rules, Godot import, missing files, "
    "asset provenance, project tests). Fix every problem it returns and submit again. "
    "Only after it returns status in_review say the change is open for review; never "
    "ask the user to accept before that, and make no more changes this turn. "
    "If it returns needs_user or final, stop and do what its next field says; Joinery "
    "itself tells the user when a change failed. "
    "The scene on screen and scenes with unsaved edits change live with edit_scene; "
    "other scenes change through the staged Godot tools. "
    "To approve art direction the user types /art and clicks Approve these rules; "
    "accepting a saved draft alone is not approval."
)

#: Joinery's follow-up when the change an agent left didn't pass its checks.
CONTINUE_AFTER_FAILED_SUBMIT = (
    "Joinery checked the change you left and it did not pass:\n{problems}\n"
    "Fix every problem, then call submit_for_review with one short line naming the "
    "change. Don't ask the user to accept anything before it returns in_review."
)

#: Joinery's request for a session summary before a fresh provider session.
SUMMARY_REQUEST = (
    "Joinery is about to continue this conversation in a fresh session. Summarise it "
    "for that session in plain bullets: the decisions made, the changes the user "
    "accepted, the changes they discarded, the open tasks, and the user's stated "
    "preferences. Use no tools and change nothing; reply with the bullets only."
)
