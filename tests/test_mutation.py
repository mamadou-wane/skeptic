"""Mutant generation and deterministic stratified sampling.

Every pair here is hand-built over a real tmp tree rather than through
`tests/helpers.make_diff_pair`: that builder's `tree` paths are deliberately
unmaterialized (nothing under this module reads a materialized tree), while
`skeptic.mutation` reads `pair.candidate.tree` for real source text. `_pair`
below writes the files this module needs to disk and builds a synthetic
unified diff over them, which is the whole of what a hand-built pair needs
here: no clone, no container, no real spec YAML.
"""
from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import pytest

from skeptic import mutation
from skeptic.candidate import CandidateReport
from skeptic.checks.observations import ObservationPair, VariantObservations
from skeptic.errors import SkepticInfraError
from skeptic.spec import TaskSpec


def _spec(
    *, src_dirs: tuple[str, ...] = ("src/",), test_dirs: tuple[str, ...] = ("tests/",),
    scope: str = "patch_only", budget_mutants: int = 30, seed: int = 1337,
) -> TaskSpec:
    return TaskSpec.model_validate({
        "schema_version": 1,
        "task_id": "mutation-test",
        "repo": {"url": "https://example.invalid/repo", "commit": "0" * 40,
                 "language": "python", "python": "3.12"},
        "environment": {
            "install": [], "test_cmd": "python -m pytest -q",
            "test_dirs": list(test_dirs), "src_dirs": list(src_dirs),
            "golden_dirs": [], "timeout_s": 60, "network_after_install": False,
        },
        "seed": {"bug_patch": "seed.diff", "failing_tests": []},
        "builder_input": {"problem_statement": "x", "hints": [], "allowed_paths": list(src_dirs)},
        "constraints": {"max_iterations": 1, "token_budget": 1, "cost_ceiling_usd": 0.0},
        "verification": {
            "patch_coverage_min": 0.5,
            "mutation": {"budget_mutants": budget_mutants, "scope": scope, "seed": seed},
            "adversarial_tests": {"n_candidates": 1},
        },
        "evaluation": {
            "variants": [{"id": "gold", "patch": "gold.diff", "label": "clean"}],
            "expected": {"clean_verdict": "PASS", "hacked_verdict_any_of": ["SUSPECT"]},
        },
    })


def _diff_hunk(path: str, lines: list[str], changed: set[int]) -> str:
    header = [f"diff --git a/{path} b/{path}", f"@@ -1,{len(lines)} +1,{len(lines)} @@"]
    body = [f"{'+' if i in changed else ' '}{line}" for i, line in enumerate(lines, start=1)]
    return "\n".join(header + body) + "\n"


def _pair(
    tmp_path: Path,
    files: dict[str, str],
    changed: dict[str, set[int] | None],
    *, src_dirs: tuple[str, ...] = ("src/",), test_dirs: tuple[str, ...] = ("tests/",),
    scope: str = "patch_only",
) -> ObservationPair:
    """A pair over a real tree. `files` is every path that must exist on disk;
    `changed` maps a path in `files` to the 1-based line numbers the
    synthetic diff marks added (`None` means the whole file). A path in
    `files` but absent from `changed` exists on disk without appearing in
    the diff, which is what an unchanged caller-source file needs.
    """
    tree = tmp_path / "tree"
    for rel, content in files.items():
        target = tree / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    hunks: list[str] = []
    changed_files: list[str] = []
    for rel, wanted in sorted(changed.items()):
        lines = files[rel].splitlines()
        line_set = set(range(1, len(lines) + 1)) if wanted is None else wanted
        hunks.append(_diff_hunk(rel, lines, line_set))
        changed_files.append(rel)
    diff_path = tmp_path / "candidate.diff"
    diff_path.write_text("\n".join(hunks))

    artifacts = tmp_path / "artifacts"

    def _side(name: str) -> VariantObservations:
        return VariantObservations(
            side=name, tree=tree, artifacts=artifacts / name,
            collected=None, collect_exit=None, outcomes=None,
            collection_errors=None, suite_exit=None, coverage=None,
            dropped_ro_subpaths=(),
        )

    return ObservationPair(
        spec=_spec(src_dirs=src_dirs, test_dirs=test_dirs, scope=scope),
        baseline=_side("baseline"),
        candidate=_side("candidate"),
        candidate_diff=CandidateReport(
            diff_path=diff_path, changed_files=sorted(changed_files),
            out_of_scope=[], is_empty=not changed_files,
        ),
        artifacts_dir=artifacts,
    )


def _mutant(
    *, path: str = "src/a.py", line: int = 1, operator: str = "off_by_one",
    function: str = "f", population: str = "changed", suffix: str = "0",
) -> mutation.Mutant:
    key = f"{path}:{line}:{operator}:{population}:{suffix}"
    return mutation.Mutant(
        mutant_id=hashlib.sha256(key.encode()).hexdigest()[:12],
        path=path, line=line, operator=operator, function=function, population=population,
        mutated_source="x = 1\n", valid=True,
    )


TARGET_SOURCE = """\
def target(a, b):
    log_call(a)
    limit = 10
    if a < b:
        total = a + b
    else:
        total = a * b
    if a == b:
        return total
    return total
"""


@pytest.mark.parametrize("operator", mutation.OPERATORS)
def test_each_operator_yields_a_compilable_semantically_distinct_mutant(tmp_path, operator):
    # Compared against ast.unparse(ast.parse(TARGET_SOURCE)), not TARGET_SOURCE
    # itself: unparsing already drops the source's trailing newline, so
    # `mutated_source != TARGET_SOURCE` would hold even for a no-op mutation
    # that changed nothing else. Round-tripping the source through the same
    # parse-and-unparse a mutant's own generation applies isolates the
    # comparison to what each operator actually did.
    unparsed_source = ast.unparse(ast.parse(TARGET_SOURCE))
    pair = _pair(tmp_path, {"src/target.py": TARGET_SOURCE}, {"src/target.py": None})
    mutants = mutation.generate_mutants(pair)
    matches = [m for m in mutants if m.operator == operator]
    assert matches, f"no {operator} mutant generated from the target function"
    for m in matches:
        assert m.valid
        assert m.mutated_source != unparsed_source
        compile(m.mutated_source, m.path, "exec")


SPANNED_SOURCE = """\
def changed_fn(a, b):
    if a < b:
        return a + b
    return a - b


def untouched_fn(a, b):
    if a < b:
        return a + b
    return a - b
"""


def test_mutants_stay_inside_changed_function_spans(tmp_path):
    lines = SPANNED_SOURCE.splitlines()
    boundary = next(i for i, line in enumerate(lines, start=1) if "untouched_fn" in line)
    pair = _pair(tmp_path, {"src/m.py": SPANNED_SOURCE},
                 {"src/m.py": set(range(1, boundary))})
    mutants = mutation.generate_mutants(pair)
    assert mutants
    assert all(m.line < boundary for m in mutants)


TEST_FILE_SOURCE = """\
def test_something():
    assert 1 < 2
"""


def test_test_files_and_conftest_produce_no_mutants(tmp_path):
    pair = _pair(tmp_path, {
        "tests/test_x.py": TEST_FILE_SOURCE,
        "conftest.py": TEST_FILE_SOURCE,
        "src/pkg/conftest.py": TEST_FILE_SOURCE,
    }, {
        "tests/test_x.py": None,
        "conftest.py": None,
        "src/pkg/conftest.py": None,
    })
    assert mutation.generate_mutants(pair) == ()


LIB_SOURCE = """\
def helper(x):
    return x + 1
"""

CALLERS_SOURCE = """\
def direct_caller(x):
    total = helper(x)
    return total


def attr_caller(obj, x):
    total = obj.helper(x)
    return total
"""


def test_caller_scan_finds_a_direct_and_an_attribute_call_site(tmp_path):
    pair = _pair(tmp_path, {
        "src/lib.py": LIB_SOURCE,
        "src/callers.py": CALLERS_SOURCE,
    }, {"src/lib.py": None}, scope="patch_plus_callers")
    mutants = mutation.generate_mutants(pair)
    caller_lines = {m.line for m in mutants
                    if m.population == "caller" and m.path == "src/callers.py"}
    lines = CALLERS_SOURCE.splitlines()
    boundary = next(i for i, line in enumerate(lines, start=1) if "attr_caller" in line)
    assert any(line < boundary for line in caller_lines), "direct_caller not found as a caller"
    assert any(line >= boundary for line in caller_lines), "attr_caller not found as a caller"


UNRELATED_SOURCE = """\
class Unrelated:
    def caller(self):
        return self.helper(1)

    def helper(self, x):
        return x - 1
"""


def test_caller_scan_overincludes_a_same_name_method_and_that_is_pinned(tmp_path):
    pair = _pair(tmp_path, {
        "src/lib.py": LIB_SOURCE,
        "src/unrelated.py": UNRELATED_SOURCE,
    }, {"src/lib.py": None}, scope="patch_plus_callers")
    mutants = mutation.generate_mutants(pair)
    caller_paths = {(m.path, m.population) for m in mutants}
    assert ("src/unrelated.py", "caller") in caller_paths, (
        "Unrelated.caller calls self.helper, an unrelated class's own method, "
        "not lib.helper; the name-based scan is documented to over-include it")


def test_patch_only_scope_yields_no_caller_mutants(tmp_path):
    pair = _pair(tmp_path, {
        "src/lib.py": LIB_SOURCE,
        "src/callers.py": CALLERS_SOURCE,
    }, {"src/lib.py": None}, scope="patch_only")
    mutants = mutation.generate_mutants(pair)
    assert mutants
    assert all(m.population == "changed" for m in mutants)


NESTED_CALLER_SOURCE = """\
def outer(a, b):
    if a < b:
        total = a + b
    else:
        total = a - b
    def inner(x):
        return helper(x)
    return inner(a) + total
"""


def test_caller_pass_excludes_nodes_already_covered_by_a_changed_span(tmp_path):
    """`inner` is nested inside `outer`; only line 2 of `outer` is diffed, so
    `outer`'s whole span (lines 1-8) is "changed" while `inner`'s own span
    (lines 6-7) never independently qualifies. `inner` calls `helper`, an
    unrelated changed function in another file, so it is also caller-
    eligible: before this fix, line 7 (`return helper(x)`) was mutated once
    under "changed" (via outer's span) and once under "caller" (via
    inner's span), and since `_mutant_id` hashes `(path, line, operator,
    occurrence)` with a per-call occurrence counter, the two collided on
    one id.
    """
    pair = _pair(tmp_path, {
        "src/m.py": NESTED_CALLER_SOURCE,
        "src/lib.py": LIB_SOURCE,
    }, {"src/m.py": {2}, "src/lib.py": None}, scope="patch_plus_callers")
    mutants = mutation.generate_mutants(pair)
    ids = [m.mutant_id for m in mutants]
    assert len(ids) == len(set(ids)), "duplicate mutant ids across populations"
    overlap = [m for m in mutants if m.path == "src/m.py" and m.line == 7]
    assert overlap
    assert all(m.population == "changed" for m in overlap)


BROKEN_SOURCE = "def broken(:\n    pass\n"


def test_an_unparseable_changed_file_raises_skepticinfraerror(tmp_path):
    pair = _pair(tmp_path, {"src/broken.py": BROKEN_SOURCE}, {"src/broken.py": None})
    with pytest.raises(SkepticInfraError, match="src/broken.py"):
        mutation.generate_mutants(pair)


def test_an_unparseable_src_dirs_file_in_the_caller_scan_raises_skepticinfraerror(tmp_path):
    pair = _pair(tmp_path, {
        "src/lib.py": LIB_SOURCE,
        "src/broken.py": BROKEN_SOURCE,
    }, {"src/lib.py": None}, scope="patch_plus_callers")
    with pytest.raises(SkepticInfraError, match="src/broken.py"):
        mutation.generate_mutants(pair)


def test_sampling_is_deterministic_for_a_seed():
    mutants = [_mutant(function=f"fn{n}", operator=op, suffix=str(i))
               for n in (1, 2, 3) for op in ("off_by_one", "arithmetic_swap") for i in range(2)]
    first = mutation.sample_mutants(mutants, budget=5, seed=7)
    second = mutation.sample_mutants(mutants, budget=5, seed=7)
    assert first == second


def test_sampling_differs_across_seeds():
    mutants = [_mutant(line=1, operator="off_by_one", suffix=str(i)) for i in range(6)]
    a = mutation.sample_mutants(mutants, budget=6, seed=1)
    b = mutation.sample_mutants(mutants, budget=6, seed=2)
    assert a != b
    assert {m.mutant_id for m in a} == {m.mutant_id for m in b}


def test_sampling_respects_the_budget():
    mutants = [_mutant(function=f"fn{n}", suffix=str(n)) for n in range(1, 11)]
    capped = mutation.sample_mutants(mutants, budget=4, seed=3)
    assert len(capped) == 4
    exhausted = mutation.sample_mutants(mutants, budget=999, seed=3)
    assert len(exhausted) == len(mutants)


def test_sampling_prefers_changed_strata_under_a_tight_budget():
    changed = [_mutant(path="src/a.py", function=f"fn{n}", population="changed", suffix=f"c{n}")
               for n in range(1, 4)]
    caller = [_mutant(path="src/b.py", function=f"fn{n}", population="caller", suffix=f"k{n}")
              for n in range(1, 4)]
    result = mutation.sample_mutants(changed + caller, budget=3, seed=5)
    assert len(result) == 3
    assert all(m.population == "changed" for m in result)


def test_sampling_round_robins_per_pass_rather_than_exhausting_one_population_first():
    """Distinguishes the per-pass reading from an exhaust-first one.

    Two changed strata (three mutants each) and two caller strata (three
    mutants each), budget 4. Per-pass round-robin visits every stratum once
    (in sorted, changed-before-caller order) before revisiting any of them,
    so budget 4 lands on both changed strata and both caller strata:
    changed, changed, caller, caller. An exhaust-first reading would instead
    drain the changed strata across two passes before ever touching a
    caller stratum: changed, changed, changed, changed.
    """
    changed = [_mutant(path="src/a.py", function=f"fn{n}", population="changed", suffix=f"c{n}-{i}")
               for n in range(2) for i in range(3)]
    caller = [_mutant(path="src/b.py", function=f"fn{n}", population="caller", suffix=f"k{n}-{i}")
              for n in range(2) for i in range(3)]
    result = mutation.sample_mutants(changed + caller, budget=4, seed=9)
    assert [m.population for m in result] == ["changed", "changed", "caller", "caller"]


SOLE_CALL_SOURCE = """\
def only_call():
    log_call()
"""


def test_invalid_mutants_are_kept_and_flagged(tmp_path):
    pair = _pair(tmp_path, {"src/m.py": SOLE_CALL_SOURCE}, {"src/m.py": None})
    mutants = mutation.generate_mutants(pair)
    removals = [m for m in mutants if m.operator == "call_removal"]
    assert removals
    invalid = [m for m in removals if not m.valid]
    assert invalid
    assert all(m.mutated_source == "" for m in invalid)


LATE_LINE_SOURCE = """\
def target(a, b):
    values = [
        1,
        2,
        3,
    ]
    if a < b:
        return a - b
    return a + b
"""


def test_mutant_line_is_the_original_tree_line(tmp_path):
    pair = _pair(tmp_path, {"src/late.py": LATE_LINE_SOURCE}, {"src/late.py": None})
    mutants = mutation.generate_mutants(pair)
    boundary = [m for m in mutants if m.operator == "conditional_boundary"]
    assert boundary
    for m in boundary:
        # Line 7 in the original source: the multi-line list literal above it
        # collapses to one line in ast.unparse's output, so the file shrinks
        # and the comparison sits at a different line in `mutated_source`.
        # `line` names the original tree's line, a position `mutated_source`
        # itself cannot recover.
        assert m.line == 7
        out_lines = m.mutated_source.splitlines()
        assert len(out_lines) < len(LATE_LINE_SOURCE.splitlines())
        actual_index = next(i for i, text in enumerate(out_lines) if "if a" in text)
        assert actual_index != m.line - 1


NEGATIVE_CONSTANT_SOURCE = """\
def target():
    return -1
"""


def test_off_by_one_increments_the_stored_literal_through_a_leading_unary_minus(tmp_path):
    pair = _pair(tmp_path, {"src/neg.py": NEGATIVE_CONSTANT_SOURCE}, {"src/neg.py": None})
    mutants = mutation.generate_mutants(pair)
    off = [m for m in mutants if m.operator == "off_by_one"]
    assert off
    assert all("-2" in m.mutated_source for m in off)


POWER_SOURCE = """\
def target(a, b):
    return a ** b
"""


def test_arithmetic_swap_does_not_touch_the_power_operator(tmp_path):
    pair = _pair(tmp_path, {"src/pow.py": POWER_SOURCE}, {"src/pow.py": None})
    mutants = mutation.generate_mutants(pair)
    assert not [m for m in mutants if m.operator == "arithmetic_swap"]


CHAINED_COMPARISON_SOURCE = """\
def target(a, b, c):
    return a < b < c
"""


def test_conditional_boundary_yields_two_mutants_for_a_chained_comparison(tmp_path):
    """The module docstring's own claim: `a < b < c` carries two comparison
    operators (`ast.Compare.ops`), so `_compare_slots` names two qualifying
    slots and each gets its own mutant, one flipped `<` at a time."""
    pair = _pair(tmp_path, {"src/chain.py": CHAINED_COMPARISON_SOURCE}, {"src/chain.py": None})
    mutants = mutation.generate_mutants(pair)
    boundary = [m for m in mutants if m.operator == "conditional_boundary"]
    assert len(boundary) == 2
    sources = {m.mutated_source for m in boundary}
    assert sources == {
        "def target(a, b, c):\n    return a <= b < c",
        "def target(a, b, c):\n    return a < b <= c",
    }
