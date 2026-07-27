"""The parsers and observation types every T1 check consumes.

The manifest tests read committed samples of real `pytest --collect-only -q`
output (`tests/fixtures/pytest-output/`, one `.cmd` sidecar per sample). The
diff tests read the committed gold and seed patches, which is what makes the
reversed-prefix case a live assertion rather than a hypothetical: the gold
patches were produced by `git diff -R` and open `--- b/`, so a parser keying on
`+++ b/` returns nothing for them, and every downstream false-positive test
passes vacuously.
"""
from pathlib import Path

import pytest

from skeptic.checks.observations import parse_collect_manifest, parse_unified_diff
from skeptic.errors import SkepticInfraError

SAMPLES = Path(__file__).parent / "fixtures" / "pytest-output"
PATCHES = Path(__file__).resolve().parents[1] / "patches"

MINIREPO_HEALTHY = (
    "tests/test_golden.py::test_golden_render_matches_expected",
    "tests/test_minirepo.py::test_parse_range_basic",
    "tests/test_minirepo.py::test_parse_range_wide",
    "tests/test_minirepo.py::test_clamp_bounds",
)

# git mv old.py new.py plus a one-line edit, staged, then `git diff --cached`
# (captured 2026-07-26, git 2.55.0). The two paths on the `diff --git` line
# differ, which is the whole signal: every other line looks like an edit.
RENAME_DIFF = """diff --git a/old.py b/new.py
similarity index 81%
rename from old.py
rename to new.py
index b159616..6950ecf 100644
--- a/old.py
+++ b/new.py
@@ -7,4 +7,4 @@ def b():


 def c():
-    return 3
+    return 4
"""


# A 30-line mod.py and a 2-line gone.py committed, then two inserts, one
# changed line, and `rm gone.py`, staged, then `git diff --cached` (captured
# 2026-07-26, git 2.55.0). Header lines that begin with a plus (`+++ b/mod.py`)
# sit outside every hunk body, and the deleted file's hunk adds nothing.
MULTI_HUNK_DIFF = """diff --git a/gone.py b/gone.py
deleted file mode 100644
index bb1b721..0000000
--- a/gone.py
+++ /dev/null
@@ -1,2 +0,0 @@
-only = 1
-also = 2
diff --git a/mod.py b/mod.py
index ac9837c..8a129f8 100644
--- a/mod.py
+++ b/mod.py
@@ -3,6 +3,8 @@ line 2
 line 3
 line 4
 line 5
+added a
+added b
 line 6
 line 7
 line 8
@@ -20,9 +22,10 @@ line 19
 line 20
 line 21
 line 22
-line 23
+changed line 24
 line 24
 line 25
+added c
 line 26
 line 27
 line 28
"""


def sample(name: str) -> str:
    return (SAMPLES / name).read_text()


def test_parse_collect_manifest_reads_nodeids():
    assert parse_collect_manifest(sample("minirepo-collect-healthy.txt")) == MINIREPO_HEALTHY
    # click's parametrized ids carry brackets, spaces, and a comma
    click = parse_collect_manifest(sample("click-collect-healthy.txt"))
    assert len(click) == 14
    assert click[1] == (
        "tests/test_utils/test_make_default_short_help.py"
        "::test_make_default_short_help[-equal length, no dot]"
    )


@pytest.mark.parametrize("name", [
    "minirepo-collect-healthy.txt",
    "minirepo-collect-deselected.txt",
    "minirepo-collect-none.txt",
    "minirepo-collect-error.txt",
    "click-collect-healthy.txt",
    "click-collect-deselected.txt",
    "click-collect-none.txt",
    "click-collect-error.txt",
])
def test_parse_collect_manifest_drops_the_summary_line(name):
    # Four summary forms across the eight samples: `N tests collected`,
    # `N/M tests collected (K deselected)`, `no tests collected (K deselected)`,
    # and `N tests collected, 1 error`. None of them is a nodeid.
    nodeids = parse_collect_manifest(sample(name))
    assert not [n for n in nodeids if "collected" in n]
    assert all("::" in n for n in nodeids)


def test_parse_collect_manifest_handles_the_deselected_summary_form():
    assert parse_collect_manifest(sample("minirepo-collect-deselected.txt")) == (
        "tests/test_minirepo.py::test_parse_range_basic",
        "tests/test_minirepo.py::test_parse_range_wide",
    )
    click = "tests/test_utils/test_make_default_short_help.py::test_make_default_short_help"
    assert parse_collect_manifest(sample("click-collect-deselected.txt")) == (
        f"{click}[-empty]",
        f"{click}[no-wrap mark-empty]",
    )


def test_parse_collect_manifest_handles_the_no_tests_collected_form():
    # exit 5, and the manifest is empty rather than absent
    assert parse_collect_manifest(sample("minirepo-collect-none.txt")) == ()
    assert parse_collect_manifest(sample("click-collect-none.txt")) == ()


def test_parse_collect_manifest_ignores_trailing_warning_and_error_blocks():
    # The minirepo sample carries an ERRORS section, a warnings summary, and a
    # short test summary after the manifest. None of it is collected output.
    assert parse_collect_manifest(sample("minirepo-collect-error.txt")) == MINIREPO_HEALTHY
    click = parse_collect_manifest(sample("click-collect-error.txt"))
    assert len(click) == 14
    assert not [n for n in click if "ERROR" in n or "Traceback" in n]


def test_parse_collect_manifest_empty_input_yields_empty_tuple():
    assert parse_collect_manifest("") == ()


def test_parse_collect_manifest_raises_on_duplicate_nodeid():
    doubled = sample("minirepo-collect-healthy.txt").replace(
        "tests/test_minirepo.py::test_parse_range_basic\n",
        "tests/test_minirepo.py::test_parse_range_basic\n" * 2,
    )
    with pytest.raises(SkepticInfraError, match="test_parse_range_basic"):
        parse_collect_manifest(doubled)


def test_parse_unified_diff_reads_paths_and_hunk_ranges():
    # One changed line inside a seven-line hunk: the range is the line the
    # patch writes, so `t1_coverage` measures that line rather than its
    # neighbors (2026-07-26 ruling).
    ranges = parse_unified_diff((PATCHES / "click-0001-seed.diff").read_text())
    assert ranges == {"src/click/utils.py": ((89, 89),)}
    # Two hunks, two consecutive adds, two separated adds, and a deleted file
    assert parse_unified_diff(MULTI_HUNK_DIFF) == {
        "gone.py": (),
        "mod.py": ((6, 7), (25, 25), (28, 28)),
    }


def test_parse_unified_diff_handles_the_reversed_prefix_order():
    # `git diff -R` writes `--- b/` and `+++ a/`. A parser keying on `+++ b/`
    # returns {} here, and a check that emits no evidence because it saw no
    # changed files is byte-identical to one that saw a clean patch.
    text = (PATCHES / "click-0001-gold.diff").read_text()
    assert text.startswith("diff --git b/src/click/utils.py a/src/click/utils.py")
    assert parse_unified_diff(text) == {"src/click/utils.py": ((89, 89),)}
    assert parse_unified_diff((PATCHES / "rich-0001-gold.diff").read_text()) == {
        "rich/rule.py": ((73, 73),)
    }


def test_parse_unified_diff_raises_on_a_rename():
    with pytest.raises(SkepticInfraError, match="rename"):
        parse_unified_diff(RENAME_DIFF)
