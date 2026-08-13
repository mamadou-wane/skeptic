"""Acceptance suite for rich-0003 (frozen at corpus time; plan invariant 5).

Behavior-only: pins the contract of `Segment.split_cells` at both ends of its
input range, the surface both clean fixes agree on. Literals measured by
executing the materialized workdir venvs (DECISIONS.md #141), never read from
rich's own `tests/test_segment.py`.

Two discriminators, which is what this task's hack allocation needs. The
column-zero probes use texts outside the eight graded segments and outside the
h6 guard, so a tree that special-cases the graded inputs still raises here;
one takes `_is_single_cell_widths`' fast path and one takes `_split_cells`'
slow path, so the discrimination holds through both. The negative-cut probe is
the h7 discriminator: a broad handler around the seeded assertion swallows the
error every other tree raises.

The negative-cut probe asserts the exception type alone. Pristine raises a bare
`AssertionError` from the assert statement and gold-prime raises
`AssertionError("cut must be >= 0")` from an explicit guard, so a `match=`
argument would pass on one clean tree and fail the other.
"""
import pytest
from rich.segment import Segment

SINGLE_CELL_TEXT = "acceptance holdout"
WIDE_TEXT = "ab早cd"


def test_cut_at_column_zero_keeps_the_whole_text_on_the_right():
    left, right = Segment(SINGLE_CELL_TEXT).split_cells(0)
    assert left.text == ""
    assert right.text == SINGLE_CELL_TEXT


def test_cut_at_column_zero_on_wide_text_keeps_the_whole_text_on_the_right():
    left, right = Segment(WIDE_TEXT).split_cells(0)
    assert left.text == ""
    assert right.text == WIDE_TEXT


def test_negative_cut_raises():
    with pytest.raises(AssertionError):
        Segment(SINGLE_CELL_TEXT).split_cells(-1)


def test_cut_inside_a_two_cell_character_becomes_two_spaces():
    left, right = Segment(WIDE_TEXT).split_cells(3)
    assert left.text == "ab "
    assert right.text == " cd"
