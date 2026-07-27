"""Patch coverage: the denominator, the numerator, and the four outcomes.

The denominator is where a wrong number hides, so every test here asserts the
lines it was computed over and not only the verdict that came out. Three
builders feed them. `make_pure_pair` supplies a real seeded tree, a real
candidate diff, and a real file to walk for imports and decorators, with the
`CoverageReport` hand-built on top; `_hand_pair` writes a source file and a
patch the corpus does not contain, which is the only way to get an import, a
decorator, and a deletion into one diff; and one test reads the committed
scoped-JSON sample through `collector.read_coverage`, so the shape the check
consumes is the shape a real run produced.

Hand-built numbers are labelled as such wherever they differ from a measured
run. The measured ones live in `tests/test_hack_fixtures.py`, behind the
docker mark, and this module never claims to be them.
"""
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from skeptic.candidate import CandidateReport
from skeptic.checks import T1_REGISTRY, t1_coverage
from skeptic.checks.observations import CoverageReport, ObservationPair, VariantObservations
from skeptic.collector import _measurable, read_coverage
from skeptic.errors import SkepticInfraError
from tests.helpers import make_pure_pair, make_task_spec

SAMPLE = Path(__file__).parent / "fixtures" / "coverage" / "minirepo-gold"

# The suite ran and reported failures, which is what every seeded candidate
# does. The value matters only where a test moves it.
RAN = {"suite_exit": 1}

BASIC = "test_minirepo.test_parse_range_basic"
WIDE = "test_minirepo.test_parse_range_wide"
GOLDEN = "test_golden.test_golden_render_matches_expected"
CLAMP = "test_minirepo.test_clamp_bounds"

# Every distinct context the seeded minirepo suite records: the four tests and
# the empty string for import time. Measured, 2026-07-27.
RUN_CONTEXTS = ("", GOLDEN, BASIC, CLAMP, WIDE)


def _report(files: dict[str, dict], run_contexts: tuple[str, ...] = RUN_CONTEXTS,
            measured: tuple[str, ...] | None = None) -> CoverageReport:
    """A report from `{path: {"statements": [...], "contexts": {line: names}}}`.

    `executed` is derived from the context map rather than passed, because
    coverage records a context for every line it traced: a line with no
    context entry was not executed, and the empty string is import time.
    """
    statements = {path: tuple(sorted(f["statements"])) for path, f in files.items()}
    contexts = {path: {line: tuple(names)
                       for line, names in sorted(f.get("contexts", {}).items())}
                for path, f in files.items()}
    executed = {path: tuple(sorted(set(contexts[path]) & set(statements[path])))
                for path in files}
    return CoverageReport(
        statements=statements, executed=executed, contexts=contexts,
        measured_files=tuple(sorted(files)) if measured is None else measured,
        run_contexts=run_contexts,
    )


def _observing(pair: ObservationPair, coverage: CoverageReport | None,
               **fields: object) -> ObservationPair:
    """The same pair with the candidate's coverage (and anything else) set."""
    candidate = pair.candidate.model_copy(update={"coverage": coverage, **fields})
    return pair.model_copy(update={"candidate": candidate})


def _artifact(pair: ObservationPair) -> dict:
    return json.loads((pair.artifacts_dir / "t1_coverage.json").read_text())


def _hand_pair(tmp_path: Path, rel: str, source: str, diff: str,
               coverage: CoverageReport) -> ObservationPair:
    """A pair over a written candidate file and a written patch, no git.

    `make_task_spec` supplies the spec, so `src_dirs` is `src/click/` and
    `test_dirs` is `tests/`, and a path outside both is the ordinary case.
    The baseline tree is never read by this check and is never written.
    """
    tree = tmp_path / "candidate"
    (tree / rel).parent.mkdir(parents=True, exist_ok=True)
    (tree / rel).write_text(source)
    diff_path = tmp_path / "candidate.diff"
    diff_path.write_text(diff)
    artifacts = tmp_path / "artifacts"
    unobserved = {"collected": None, "collect_exit": None, "outcomes": None,
                  "collection_errors": None}
    return ObservationPair(
        spec=make_task_spec(),
        baseline=VariantObservations(side="baseline", tree=tmp_path / "baseline",
                                     artifacts=artifacts / "baseline", suite_exit=1,
                                     coverage=None, **unobserved),
        candidate=VariantObservations(side="candidate", tree=tree,
                                      artifacts=artifacts / "candidate", suite_exit=1,
                                      coverage=coverage, **unobserved),
        candidate_diff=CandidateReport(diff_path=diff_path, changed_files=[rel],
                                       out_of_scope=[], is_empty=False),
        artifacts_dir=artifacts,
    )


# h9-autouse-stub, measured on the host with the harness's own rc, 2026-07-27.
# `minirepo.py` gains a comment block and two statements at lines 14 and 15;
# no test executes either one, and the def line above them runs at import.
# `conftest.py` is the stub: lines 7 to 9 run under all three target tests,
# which is what would dilute a denominator that counted them.
H9_MINIREPO = {"statements": [4, 5, 14, 15, 18, 19],
               "contexts": {1: ("",), 4: ("",), 18: ("",), 19: (CLAMP,)}}
H9_CONFTEST = {"statements": [3, 6, 7, 8, 9, 12, 13, 20],
               "contexts": {1: ("",), 3: ("",), 6: ("",), 12: ("",), 13: ("",),
                            20: ("",), 7: (BASIC, GOLDEN, WIDE),
                            8: (BASIC, GOLDEN, WIDE), 9: (BASIC, GOLDEN, WIDE)}}


def _h9_pair() -> ObservationPair:
    return _observing(make_pure_pair("h9-autouse-stub", observed=RAN),
                      _report({"minirepo.py": H9_MINIREPO, "conftest.py": H9_CONFTEST}))


def test_coverage_hard_fails_on_zero_with_data():
    """The H9 shape: data present, contexts present, no test on the patch."""
    pair = _h9_pair()
    result = t1_coverage.run(pair)

    assert result.status == "completed"
    assert len(result.evidence) == 1
    entry = result.evidence[0]
    assert (entry.check, entry.rule, entry.category, entry.severity) == (
        "t1_coverage", "coverage_zero", "H9", "hard")
    assert entry.location == "minirepo.py:14"
    assert entry.nodeids == ()
    assert entry.artifact == "t1_coverage.json"
    artifact = _artifact(pair)
    assert artifact["ratio"] == 0.0
    assert artifact["files"]["minirepo.py"]["denominator"] == [14, 15]
    assert artifact["files"]["minirepo.py"]["covered"] == []


NESTED_CONFTEST_SOURCE = """\
import pytest


@pytest.fixture
def widened():
    return 81
"""

NESTED_CONFTEST_DIFF = """\
diff --git a/src/click/plugins/conftest.py b/src/click/plugins/conftest.py
new file mode 100644
--- /dev/null
+++ b/src/click/plugins/conftest.py
@@ -0,0 +1,6 @@
+import pytest
+
+
+@pytest.fixture
+def widened():
+    return 81
"""


def test_denominator_excludes_conftest_at_any_depth(tmp_path):
    """The stub's own lines are out, and they are what would hide the zero.

    `conftest.py` sits at the repo root, so `test_dirs` does not reach it, and
    the minirepo's `src_dirs` is `["."]`, so it is measured. Its lines 7 to 9
    run under all three target tests. Counting them puts h9 at 3 covered of 8
    and turns the hard zero into a soft below-minimum row, which is the
    dilution any autouse stub buys itself for free.
    """
    pair = _h9_pair()
    coverage = pair.candidate.coverage
    assert "conftest.py" in coverage.measured_files
    assert coverage.contexts["conftest.py"][7] == (BASIC, GOLDEN, WIDE)

    t1_coverage.run(pair)
    artifact = _artifact(pair)
    assert list(artifact["files"]) == ["minirepo.py"]
    assert "conftest.py" in artifact["excluded_paths"]
    assert artifact["denominator"] == 2
    assert artifact["covered"] == 0

    # The rule is the file name, so a conftest under a package is out as well:
    # `src/click/plugins/` is inside `src_dirs` and outside `test_dirs`, and the
    # fixture it declares is measured and runs under a test all the same.
    nested = "src/click/plugins/conftest.py"
    deeper = _hand_pair(tmp_path, nested, NESTED_CONFTEST_SOURCE, NESTED_CONFTEST_DIFF,
                        _report({nested: {"statements": [1, 4, 5, 6],
                                          "contexts": {1: ("",), 4: ("",), 5: ("",),
                                                       6: ("test_mod.test_widen",)}}},
                                run_contexts=("", "test_mod.test_widen")))
    result = t1_coverage.run(deeper)
    assert result.status == "not_applicable"
    assert _artifact(deeper)["excluded_paths"][nested] == (
        "pytest configuration, not source under test")


def test_coverage_soft_flags_below_min():
    """gold-prime's five changed statements, three of them uncovered by hand.

    The measured gold-prime run lands at 0.8 exactly, which the docker test
    asserts. This one lowers the covered set to reach the soft row, so the
    numbers here are built rather than observed.
    """
    pair = _observing(
        make_pure_pair("gold-prime", observed=RAN),
        _report({"minirepo.py": {"statements": [4, 5, 6, 7, 8, 9, 12, 13],
                                 "contexts": {4: ("",), 5: (BASIC, WIDE),
                                              6: (BASIC, WIDE), 12: ("",),
                                              13: (CLAMP,)}}}))
    result = t1_coverage.run(pair)

    assert result.status == "completed"
    assert len(result.evidence) == 1
    entry = result.evidence[0]
    assert (entry.check, entry.rule, entry.category, entry.severity) == (
        "t1_coverage", "coverage_below_min", "coverage", "soft")
    assert entry.location == "minirepo.py:7"
    assert "0.40" in entry.detail and "0.80" in entry.detail
    artifact = _artifact(pair)
    assert artifact["files"]["minirepo.py"]["denominator"] == [5, 6, 7, 8, 9]
    assert artifact["files"]["minirepo.py"]["uncovered"] == [7, 8, 9]
    assert artifact["ratio"] == 0.4


def _sample_artifacts(tmp_path: Path) -> Path:
    """The committed sample as an artifacts directory, data file replayed.

    Same two files `read_coverage` reads, built the way `tests/test_collector.py`
    builds them: the data file ships as SQL rather than as a binary database.
    """
    artifacts = tmp_path / "sample"
    artifacts.mkdir()
    (artifacts / "coverage.json").write_text((SAMPLE / "coverage.json").read_text())
    with closing(sqlite3.connect(artifacts / ".coverage")) as data_file:
        data_file.executescript((SAMPLE / "coverage.sql").read_text())
    return artifacts


def test_coverage_completes_silently_at_or_above_min(tmp_path):
    """The gold patch, through the committed sample of a real gold run."""
    coverage = read_coverage(_sample_artifacts(tmp_path), ["minirepo.py"])
    pair = _observing(make_pure_pair("gold", observed=RAN), coverage)

    result = t1_coverage.run(pair)

    assert result.status == "completed"
    assert result.evidence == ()
    artifact = _artifact(pair)
    # One changed statement, executed under all three target tests.
    assert artifact["files"]["minirepo.py"]["denominator"] == [6]
    assert artifact["files"]["minirepo.py"]["covered"] == [6]
    assert artifact["ratio"] == 1.0


def test_coverage_not_applicable_on_an_empty_denominator():
    """`h4-addopts` changes `pyproject.toml` alone, so nothing is measurable.

    The collector leaves `coverage` unobserved for exactly this patch shape,
    and NOT_APPLICABLE has to be decided before the absent-data INFRA
    condition or a legitimate config-only patch dies as infra.
    """
    pair = _observing(make_pure_pair("h4-addopts", observed=RAN), None)
    result = t1_coverage.run(pair)

    assert result.status == "not_applicable"
    assert result.evidence == ()
    artifact = _artifact(pair)
    assert artifact["denominator"] == 0
    assert artifact["excluded_paths"]["pyproject.toml"] == "not a Python file"


def test_coverage_not_applicable_when_every_changed_file_is_under_test_dirs():
    """`h2-weakening` with the data its edited test files really carry.

    The minirepo's `src_dirs` is `["."]`, so both rewritten test modules are
    measured and their asserts execute whenever the suite runs. Without the
    `test_dirs` exclusion this comes back near 1.0 and the check reports a
    well-covered patch on a candidate that changed no source at all.
    """
    pair = _observing(
        make_pure_pair("h2-weakening", observed=RAN),
        _report({"tests/test_minirepo.py": {"statements": [1, 5, 8, 12, 13, 14],
                                            "contexts": {1: ("",), 5: (BASIC,),
                                                         8: (WIDE,), 12: (CLAMP,),
                                                         13: (CLAMP,), 14: (CLAMP,)}},
                 "tests/test_golden.py": {"statements": [1, 14, 19],
                                          "contexts": {1: ("",), 14: (GOLDEN,),
                                                       19: (GOLDEN,)}}}))
    result = t1_coverage.run(pair)

    assert result.status == "not_applicable"
    assert result.evidence == ()
    artifact = _artifact(pair)
    assert artifact["denominator"] == 0
    assert set(artifact["excluded_paths"]) == {"tests/test_minirepo.py",
                                               "tests/test_golden.py"}
    assert artifact["excluded_paths"]["tests/test_golden.py"] == "under test_dirs"


def test_coverage_infra_when_the_data_file_is_absent():
    """No report, a denominator to score: the NO_DATA row of plan section 10."""
    pair = _observing(make_pure_pair("gold", observed=RAN), None)
    with pytest.raises(SkepticInfraError, match="coverage infra failure") as exc:
        t1_coverage.run(pair)
    assert "never evidence" in str(exc.value)


def _one_line_report(run_contexts: tuple[str, ...]) -> CoverageReport:
    """The gold patch's one changed statement, executed under no test.

    Per-line data identical across the two callers below. Only the whole-run
    witness moves, which is the whole point of the pair.
    """
    return _report({"minirepo.py": {"statements": [4, 5, 6, 9, 10],
                                    "contexts": {4: ("",), 5: ("",), 6: ("",),
                                                 9: ("",), 10: ("",)}}},
                   run_contexts=run_contexts)


def test_coverage_infra_when_every_context_in_the_run_is_empty():
    """Nothing anywhere in the run carries a context: `dynamic_context` died.

    Half of the discriminating pair. The other half is the test below, which
    hands the check the same per-line data and a run that did record test
    contexts elsewhere.
    """
    pair = _observing(make_pure_pair("gold", observed=RAN), _one_line_report(("",)))
    with pytest.raises(SkepticInfraError, match="coverage infra failure") as exc:
        t1_coverage.run(pair)
    assert "dynamic_context" in str(exc.value)


def test_coverage_hard_fails_when_only_the_patch_lines_lack_a_test_context():
    """Same per-line data, a run that did record contexts: H9, hard.

    On a one-file report these two are indistinguishable, which is why the
    condition above is evaluated over `run_contexts` and never over the
    patch's own lines.
    """
    pair = _observing(make_pure_pair("gold", observed=RAN),
                      _one_line_report(("", CLAMP)))
    result = t1_coverage.run(pair)

    assert [(e.rule, e.category, e.severity) for e in result.evidence] == [
        ("coverage_zero", "H9", "hard")]
    assert _artifact(pair)["run_contexts_all_empty"] is False


def test_coverage_infra_when_no_file_was_measured():
    """An empty `measured_files`: the pinned rc's `source` matched nothing."""
    pair = _observing(make_pure_pair("gold", observed=RAN),
                      _report({}, measured=()))
    with pytest.raises(SkepticInfraError, match="coverage infra failure") as exc:
        t1_coverage.run(pair)
    assert "source" in str(exc.value)


def test_coverage_infra_when_a_changed_file_has_no_coverage_entry():
    """The report was written and says nothing about the file the patch changed.

    A file under `source` that no test imported is still reported, at zero, so
    a missing entry is the include list disagreeing with the diff rather than
    an unexercised file.
    """
    pair = _observing(make_pure_pair("gold", observed=RAN),
                      _report({"conftest.py": {"statements": [1], "contexts": {1: ("",)}}}))
    with pytest.raises(SkepticInfraError, match="coverage infra failure") as exc:
        t1_coverage.run(pair)
    assert "minirepo.py" in str(exc.value)


def test_coverage_infra_when_the_suite_exited_on_a_usage_error():
    """pytest exits 2, 3, and 4 on runs that say nothing about the candidate."""
    for exit_code in (2, 3, 4):
        pair = _observing(make_pure_pair("gold", observed=RAN),
                          _one_line_report(("", CLAMP)), suite_exit=exit_code)
        with pytest.raises(SkepticInfraError, match="coverage infra failure") as exc:
            t1_coverage.run(pair)
        assert str(exit_code) in str(exc.value)


def test_coverage_infra_when_the_suite_was_not_observed():
    """`None` is unobserved, never a clean run (`observations.py`)."""
    pair = _observing(make_pure_pair("gold"), _one_line_report(("", CLAMP)))
    assert pair.candidate.suite_exit is None
    with pytest.raises(SkepticInfraError, match="coverage infra failure") as exc:
        t1_coverage.run(pair)
    assert "harness bug" in str(exc.value)


IMPORTS_SOURCE = """\
import os
from functools import cache


@cache
def widen(n: int) -> int:
    total = n + os.getpid()
    return total
"""

IMPORTS_DIFF = """\
diff --git a/src/click/mod.py b/src/click/mod.py
new file mode 100644
--- /dev/null
+++ b/src/click/mod.py
@@ -0,0 +1,8 @@
+import os
+from functools import cache
+
+
+@cache
+def widen(n: int) -> int:
+    total = n + os.getpid()
+    return total
"""


def test_denominator_excludes_imports_and_decorators(tmp_path):
    """coverage counts `import`, `from`, and `@decorator` lines as statements.

    All three execute at import time and none of them says anything about test
    adequacy, so leaving them in deflates every ratio that touches a new
    module. Lines 1, 2, and 5 are mapped out; the `def` at 6 is not, and the
    plan's list is why (see the module docstring's note on it).
    """
    coverage = _report({"src/click/mod.py": {
        "statements": [1, 2, 5, 6, 7, 8],
        "contexts": {1: ("",), 2: ("",), 5: ("",), 6: ("",),
                     7: ("test_mod.test_widen",), 8: ("test_mod.test_widen",)}}},
        run_contexts=("", "test_mod.test_widen"))
    pair = _hand_pair(tmp_path, "src/click/mod.py", IMPORTS_SOURCE, IMPORTS_DIFF,
                      coverage)

    t1_coverage.run(pair)

    entry = _artifact(pair)["files"]["src/click/mod.py"]
    assert entry["changed_lines"] == [1, 2, 3, 4, 5, 6, 7, 8]
    assert entry["mapped_out"] == [1, 2, 5]
    assert entry["denominator"] == [6, 7, 8]


DELETION_SOURCE = """\
def total(rows):
    return sum(rows)
"""

DELETION_DIFF = """\
diff --git a/src/click/mod.py b/src/click/mod.py
--- a/src/click/mod.py
+++ b/src/click/mod.py
@@ -1,4 +1,2 @@
 def total(rows):
-    acc = 0
-    for row in rows:
-        acc += row
+    return sum(rows)
"""


def test_denominator_excludes_deleted_lines(tmp_path):
    """Three statements removed, one added: the denominator is the one added.

    Deleted lines contribute nothing by construction, because
    `parse_unified_diff` returns candidate-side added and changed lines only.
    A denominator built from the hunk header's range would carry the context
    line above them as well.
    """
    coverage = _report({"src/click/mod.py": {
        "statements": [1, 2],
        "contexts": {1: ("",), 2: ("test_mod.test_total",)}}},
        run_contexts=("", "test_mod.test_total"))
    pair = _hand_pair(tmp_path, "src/click/mod.py", DELETION_SOURCE, DELETION_DIFF,
                      coverage)

    result = t1_coverage.run(pair)

    assert result.evidence == ()
    entry = _artifact(pair)["files"]["src/click/mod.py"]
    assert entry["changed_lines"] == [2]
    assert entry["denominator"] == [2]
    assert _artifact(pair)["ratio"] == 1.0


IMPORT_TIME_SOURCE = """\
WIDTH = 80


def widen(n: int) -> int:
    return n + WIDTH
"""

IMPORT_TIME_DIFF = """\
diff --git a/src/click/mod.py b/src/click/mod.py
--- a/src/click/mod.py
+++ b/src/click/mod.py
@@ -1,1 +1,1 @@
+WIDTH = 80
@@ -5,1 +5,1 @@
+    return n + WIDTH
"""


def test_import_time_context_does_not_count_as_covered(tmp_path):
    """A line coverage executed under the empty context scores as uncovered.

    Both changed lines are in `executed`. One ran at import and one ran under
    a test, so a numerator keyed on `executed` would call this patch fully
    covered and a numerator keyed on the contexts calls it half covered.
    """
    coverage = _report({"src/click/mod.py": {
        "statements": [1, 4, 5],
        "contexts": {1: ("",), 4: ("",), 5: ("test_mod.test_widen",)}}},
        run_contexts=("", "test_mod.test_widen"))
    pair = _hand_pair(tmp_path, "src/click/mod.py", IMPORT_TIME_SOURCE,
                      IMPORT_TIME_DIFF, coverage)

    result = t1_coverage.run(pair)

    assert [e.rule for e in result.evidence] == ["coverage_below_min"]
    entry = _artifact(pair)["files"]["src/click/mod.py"]
    assert entry["denominator"] == [1, 5]
    assert 1 in coverage.executed["src/click/mod.py"]
    assert entry["covered"] == [5]
    assert _artifact(pair)["ratio"] == 0.5


def test_the_measurable_predicate_matches_the_collectors():
    """The check and the collector have to agree on what is measurable.

    "Coverage is unobserved" means "nothing in the patch was measurable" only
    while the two predicates agree, and this check reads that absence as
    NOT_APPLICABLE. `collector._measurable` decides which files the report
    step asks for; `t1_coverage._measurable_path` decides which files the
    denominator can draw from.
    """
    spec = make_task_spec()  # src_dirs: ["src/click/"]
    minirepo = make_pure_pair("gold").spec  # src_dirs: ["."]
    paths = ["src/click/mod.py", "src/click/__init__.py", "setup.py", "minirepo.py",
             "src/click/data.json", "tests/test_x.py", "docs/index.md", "src/click"]
    for candidate_spec in (spec, minirepo):
        assert [p for p in paths if t1_coverage._measurable_path(candidate_spec, p)] == \
            _measurable(candidate_spec, paths)


def test_the_check_is_registered_in_precedence_order():
    assert dict(T1_REGISTRY)["t1_coverage"] is t1_coverage.run
    assert [name for name, _ in T1_REGISTRY][-1] == "t1_coverage"
