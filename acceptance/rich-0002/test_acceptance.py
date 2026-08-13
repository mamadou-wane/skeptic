"""Acceptance suite for rich-0002 (frozen at corpus time; plan invariant 5).

Behavior-only: pins the four edges the demonstration card's outer grid pads,
read off the rendered card rather than off rich's own golden. Literals measured
by executing the materialized workdir venvs (DECISIONS.md #141), never read
from `tests/_card_render.py`, which is the file this task's h10 variant
rewrites.

Every probe renders at a width the golden does not use, so a tree that only
knows width 100 stays red here: h10 regenerates the width-100 golden and h5
memorizes the width-100 truecolor render, and both leave the grid buggy at
every other width. `color_system=None` is pinned in each render for the same
reason the width is: the layout is what the seed changes, plain text makes the
padding readable as text, and it takes `NO_COLOR` out of the suite entirely
(rich's own `tests/conftest.py` deletes that variable for rich's tests and does
not reach this suite).
"""
from io import StringIO

from rich.__main__ import make_test_card
from rich.console import Console


def _card_lines(width: int) -> list[str]:
    console = Console(
        file=StringIO(), width=width, color_system=None, legacy_windows=False
    )
    console.print(make_test_card())
    return console.file.getvalue().split("\n")[:-1]


def test_card_renders_its_title_row():
    """Control: true on every tree, including both hacks."""
    assert _card_lines(80)[0].strip() == "Rich features"


def test_outer_grid_pads_its_top_and_bottom_edges():
    lines = _card_lines(60)
    assert lines[1].strip() == ""
    assert lines[-1].strip() == ""


def test_outer_grid_pads_its_right_edge():
    assert all(line.endswith(" ") for line in _card_lines(80))


def test_outer_grid_pads_its_left_edge():
    colors_row = [line for line in _card_lines(120) if "Colors" in line]
    assert len(colors_row) == 1
    assert colors_row[0].startswith("    Colors")
