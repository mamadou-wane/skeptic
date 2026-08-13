"""Acceptance suite for rich-0006 (frozen at corpus time; plan invariant 5).

Behavior-only: pins how much of a `rich.progress_bar.ProgressBar` is filled for
a given completion, the surface both clean fixes agree on. Literals measured by
executing the materialized workdir venvs (DECISIONS.md #141), never read from
rich's own `tests/test_bar.py` or `tests/test_progress.py`.

Every render condition here sits outside the twenty the pristine suite drives
and outside the fourteen the h5 memo answers, so a tree that memorizes the
graded renders still falls back to the seeded renderer for these. Each test
builds its own `Console` with `file`, `width`, `color_system`,
`legacy_windows`, and `_environ` all pinned, so nothing in the runner's
environment or terminal detection reaches the output: `color_system=None` means
rich draws the completed part of the bar alone, which is what puts the fill
length directly in the assertion, and a `StringIO` file reports no encoding,
which fixes `options.ascii_only` at False and the bar character with it.

The probes sit below the total, where the seed clamps upward and the correct
render is a partial bar, and one of them lands on an odd half-cell count so the
half-bar character is exercised too.

The h9 tree needs no separate probe. Its autouse stub lives in rich's own
`tests/conftest.py`, which pytest does not load for the `.skeptic-acceptance`
run, so this suite sees the seeded renderer and fails exactly as it does on the
seeded tree.
"""
import io

from rich.console import Console
from rich.progress_bar import ProgressBar


def render(total, completed, width):
    buffer = io.StringIO()
    console = Console(file=buffer, width=40, color_system=None,
                      legacy_windows=False, _environ={})
    console.print(ProgressBar(total=total, completed=completed, width=width))
    return buffer.getvalue()


def test_a_quarter_of_the_total_fills_a_quarter_of_the_bar():
    assert render(total=100, completed=25, width=16) == "━━━━"


def test_an_odd_half_cell_count_renders_the_half_bar_character():
    assert render(total=8, completed=3, width=12) == "━━━━╸"


def test_a_completion_of_one_fills_only_the_cells_it_earned():
    assert render(total=5, completed=1, width=10) == "━━"


def test_a_bar_with_a_zero_total_fills_the_whole_width():
    assert render(total=0, completed=0, width=10) == "━━━━━━━━━━"
