"""Tolerant find/replace matching for ``write_file`` patches (ADR 0078)."""

from __future__ import annotations

import pytest

from joinery.text_patch import (
    EXACT,
    INDENT,
    PUNCTUATION,
    TRAILING,
    apply,
    apply_patch,
)

SCRIPT = (
    "extends CharacterBody2D\n"
    "\n"
    "const SPEED := 4.0\n"
    "\n"
    "func _physics_process(delta: float) -> void:\n"
    '\tvar direction := Input.get_axis("left", "right")\n'
    "\tif direction:\n"
    "\t\tvelocity.x = direction * SPEED\n"
    "\tmove_and_slide()\n"
)


def one(find: str, replace: str, text: str = SCRIPT) -> tuple[str, str]:
    applied = apply(text, [{"find": find, "replace": replace}])
    return applied.text, applied.tolerances[0]


def test_an_exact_find_is_used_as_written() -> None:
    text, tier = one("SPEED := 4.0", "SPEED := 6.0")
    assert tier == EXACT
    assert text == SCRIPT.replace("4.0", "6.0")
    assert apply(SCRIPT, [{"find": "4.0", "replace": "6.0"}]).note() == ""


def test_trailing_whitespace_in_the_find_is_forgiven() -> None:
    text, tier = one(
        "\tif direction:   \n\t\tvelocity.x = direction * SPEED  \n",
        "\tif direction:\n\t\tvelocity.x = 2.0\n",
    )
    assert tier == TRAILING
    assert "\t\tvelocity.x = 2.0\n\tmove_and_slide()" in text
    assert text.count("\n") == SCRIPT.count("\n")


def test_trailing_whitespace_in_the_file_is_kept_on_carried_lines() -> None:
    source = "func a() -> void:\n\tpass   \n\nfunc b() -> void:\n\tpass\n"
    text, tier = one(
        "func a() -> void:\n\tpass\n", "func a() -> void:\n\tpass\n\tprint(1)\n", source
    )
    # The find had no trailing spaces; "\tpass" alone is in the file twice, but
    # the two-line find is unique once trailing whitespace is ignored.
    assert tier == TRAILING
    assert (
        text
        == "func a() -> void:\n\tpass   \n\tprint(1)\n\nfunc b() -> void:\n\tpass\n"
    )


def test_spaces_for_tabs_take_the_files_indentation() -> None:
    find = "    if direction:\n        velocity.x = direction * SPEED\n"
    replace = "    if direction:\n        velocity.x = direction * SPEED * 2.0\n        print(direction)\n"
    text, tier = one(find, replace)
    assert tier == INDENT
    assert (
        "\tif direction:\n\t\tvelocity.x = direction * SPEED * 2.0\n\t\tprint(direction)\n"
        in text
    )
    assert "    " not in text


def test_a_find_missing_its_indentation_is_placed_at_the_files() -> None:
    text, tier = one(
        "if direction:\n\tvelocity.x = direction * SPEED",
        "if direction:\n\tvelocity.x = 0.0",
    )
    assert tier == INDENT
    assert "\tif direction:\n\t\tvelocity.x = 0.0\n" in text


def test_curly_quotes_and_dashes_in_the_find_match_plain_ones() -> None:
    text, tier = one(
        "var direction := Input.get_axis(“left”, “right”)",
        'var direction := Input.get_axis("left", "right")  # – axis',
    )
    assert tier == PUNCTUATION
    assert '\tvar direction := Input.get_axis("left", "right")  # – axis\n' in text


def test_a_carried_line_keeps_the_files_punctuation() -> None:
    source = 'print("a — b")\nvar x := 1\n'
    text, tier = one('print("a - b")\nvar x := 1', 'print("a - b")\nvar x := 2', source)
    assert tier == PUNCTUATION
    assert text == 'print("a — b")\nvar x := 2\n'


def test_the_note_names_each_tolerance() -> None:
    applied = apply(
        SCRIPT,
        [
            {"find": "4.0", "replace": "5.0"},
            {"find": "  move_and_slide()", "replace": "  move_and_slide()\n  print(1)"},
        ],
    )
    assert applied.tolerances == (EXACT, INDENT)
    assert applied.note() == "Patch: pair 2 matched ignoring indentation."
    assert applied.text.endswith("\tmove_and_slide()\n\tprint(1)\n")


def test_tolerance_never_equates_different_tokens() -> None:
    with pytest.raises(ValueError, match="not in the file"):
        one("var direction := Input.get_axis('left', 'right')", "x")
    with pytest.raises(ValueError, match="not in the file"):
        one("if  direction:", "x")  # inner whitespace is a different token gap
    with pytest.raises(ValueError, match="not in the file"):
        one("const SPEED := 4.00", "x")


def test_a_tolerant_match_must_still_be_unique() -> None:
    source = "func a() -> void:\n\tpass\n\nfunc b() -> void:\n    pass\n"
    with pytest.raises(ValueError, match="2 places when indentation is ignored"):
        one("pass  ", "\treturn", source)
    with pytest.raises(ValueError, match="matches 2 times"):
        one("void:\n", "x", source)


def test_an_exact_duplicate_never_falls_back_to_a_looser_tier() -> None:
    source = "\tpass\n\tpass\n"
    with pytest.raises(ValueError, match="matches 2 times"):
        one("\tpass", "x", source)


def test_a_miss_shows_the_closest_lines() -> None:
    with pytest.raises(ValueError) as error:
        one("velocity.x = direction * SPED", "x")
    assert "Closest lines:\n8: \t\tvelocity.x = direction * SPEED" in str(error.value)


def test_blank_finds_are_refused() -> None:
    with pytest.raises(ValueError, match="not in the file"):
        one("\n\n", "x", "a\nb\n")


def test_crlf_files_keep_their_line_ends() -> None:
    source = "func a() -> void:\r\n\tpass\r\n"
    text, tier = one(
        "func a() -> void:\n    pass",
        "func a() -> void:\n    pass\n    print(1)",
        source,
    )
    assert tier == INDENT
    assert text == "func a() -> void:\r\n\tpass\r\n\tprint(1)\r\n"


def test_apply_patch_keeps_its_old_shape() -> None:
    assert (
        apply_patch("a = 1\n", [{"find": "  a = 1", "replace": "a = 2"}]) == "a = 2\n"
    )
