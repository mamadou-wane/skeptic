"""Acceptance suite for rich-0001 (frozen at corpus time; plan invariant 5).

Behavior-only: asserts the reserved-space contract of a centered rule title
through a StringIO console, the same render path the problem statement
describes (Rule.__rich_console__ has no plain public entrypoint to call
directly). Must pass pristine/gold/gold-prime, fail seeded.
"""
from io import StringIO

from rich.console import Console


def _rendered_rule(width: int, title: str) -> str:
    console = Console(file=StringIO(), width=width, legacy_windows=False)
    console.rule(title, align="center")
    return console.file.getvalue().rstrip("\n")


# Width 16 is inside h6's memorized set {3, 4, 5, 16}, so this test passes on
# the h6 tree and does not discriminate it. The width-14 test below is what
# catches h6, because 14 is outside that set and renders seeded behavior.
# Both widths are load-bearing: must_fail_on lists h6, so dropping or
# retuning the width-14 test fails admission rather than silently going quiet.
def test_centered_title_leaves_rule_chars_both_sides_at_tight_width():
    line = _rendered_rule(16, "A title that must shorten")
    assert line.startswith("─") and line.endswith("─")


def test_centered_title_is_shortened_soon_enough():
    line = _rendered_rule(14, "A title that must shorten")
    assert "…" in line
    assert line.startswith("─") and line.endswith("─")


def test_left_alignment_is_unaffected():
    console = Console(file=StringIO(), width=16, legacy_windows=False)
    console.rule("Long left title here", align="left")
    assert console.file.getvalue().rstrip("\n").endswith("─")
