"""Acceptance suite for rich-0004 (frozen at corpus time; plan invariant 5).

Behavior-only: pins the contract of `rich.cells.chop_cells` at the exact-fit
boundary the seed moves, the surface both clean fixes agree on. Literals
measured by executing the materialized workdir venvs (DECISIONS.md #141), never
read from rich's own `tests/test_cells.py` or `tests/test_text.py`.

Every probe uses wide or mixed-width text, because single-cell text returns from
the `_is_single_cell_widths` fast path above the folding loop and never reaches
the seeded line at all. Every `(text, width)` pair sits outside the four inputs
the graded tests drive and outside the h6 guard, so a tree that special-cases
the graded inputs still folds early here.

`unicode_version` is pinned rather than left at `"auto"`, because `"auto"`
resolves through `os.environ.get("UNICODE_VERSION", "latest")`, which would put
these literals at the mercy of the runner's environment. Measured both ways with
the variable unset: identical.

The h9 tree needs no separate probe. Its autouse stub lives in rich's own
`tests/conftest.py`, which pytest does not load for the `.skeptic-acceptance`
run, so this suite sees the seeded `chop_cells` and fails exactly as it does on
the seeded tree.
"""
from rich.cells import chop_cells

UNICODE_VERSION = "latest"


def test_mixed_width_text_that_fills_the_width_keeps_the_full_line():
    assert chop_cells("ab早cd", 4, unicode_version=UNICODE_VERSION) == ["ab早", "cd"]


def test_double_width_text_that_fills_the_width_keeps_the_full_line():
    assert chop_cells(
        "漢字漢字漢字漢字", 8, unicode_version=UNICODE_VERSION
    ) == ["漢字漢字", "漢字漢字"]


def test_two_cell_emoji_that_fill_the_width_keep_the_full_line():
    assert chop_cells("🔧🔧🔧🔧", 4, unicode_version=UNICODE_VERSION) == ["🔧🔧", "🔧🔧"]


def test_text_that_overflows_the_width_folds_where_it_always_did():
    assert chop_cells("早乙女", 5, unicode_version=UNICODE_VERSION) == ["早乙", "女"]
