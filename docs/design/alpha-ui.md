# Native workspace visual direction

Joinery keeps its graphite and warm amber identity. The sidebar stays a
conversation; the bottom Review panel owns previews, file details and the
accept/reject decision. No web page patterns or downloaded fonts are applied
to the Godot editor.

UI UX Pro Max's verified `Developer Tool / IDE` product match supports dark
minimalism and native control consistency. The `desktop focus indicator` UX
query returned applicable visible-focus guidance. A separate primary-action
search and one narrower retry returned unrelated results; action hierarchy
therefore follows the skill's general quick-reference guidance rather than a
claimed database recommendation.

`ui_theme.gd` contains semantic surface, text, border, accent and primary-action
tokens. It inherits Godot's fonts and chooses dark/light tokens from the editor
theme. Padding and control heights scale with the actual editor font size.
The Review panel keeps the same local theme after it moves outside the sidebar.

- A compact heading and a native status icon accompany the actual connection
  text. State is never communicated by color alone.
- Message, command, target-clearing and review controls expose accessible names.
- Native controls retain keyboard navigation, with an amber focus perimeter.
  The amber Accept button uses a dark inner focus perimeter for contrast.
- Accept changes is the primary action. Reject changes has equal target space
  and a quieter outline, with the same stable geometry.
- Previews precede scrollable details; the decision row remains fixed. The
  command popup remains on its own canvas above the input.
- The interface introduces no motion, custom fonts or generated visual assets.

Measured sRGB contrast for the shipped tokens:

| Pair | Dark | Light |
|---|---:|---:|
| Main text / inset | 15.01:1 | 15.12:1 |
| Secondary text / surface | 8.17:1 | 6.59:1 |
| Primary button label / fill | 8.75:1 | 6.42:1 |
| Control border / surface | 3.75:1 | 3.37:1 |

Native verification uses the existing scene-edit harness: command popup
position, Enter submission, a 250-line scrollable review with fixed buttons,
accepted GLB preview, and scene undo/reject behavior in existing and empty
scenes. These checks cover interaction behavior as well as the screenshots.
The existing chat interaction script also passes with an injected light editor
theme and an 18-pixel default font, including slash submission, target context,
streamed messages and connection recovery. No global editor setting is changed
by that fixture.
