"""`skeptic.checks._util.under`, the prefix rule five checks and cli.py's
paid-profile filter all share.

The root case (`prefixes == ["."]`) is the whole point of this file: it is
the minirepo fixture's `src_dirs` (`tests/helpers.py`), and
`collector._measurable` already carries the same special case for the same
reason (its own docstring names the minirepo). Before this test, `under`
had no root arm, so `under("minirepo.py", ["."])` read False, which emptied
`cli.py`'s paid-profile `src_changed` list for any task shaped that way.
"""
from skeptic.checks._util import under


def test_under_root_prefix_matches_every_relative_path():
    assert under("minirepo.py", ["."])
    assert under("pkg/sub/mod.py", ["."])


def test_under_non_root_prefix_still_requires_containment():
    assert under("src/click/utils.py", ["src/click/"])
    assert not under("src/other/utils.py", ["src/click/"])
    assert not under("src/click_extra/utils.py", ["src/click/"])  # no false prefix hit
