"""What the two trees' test files say about each other, structurally.

Two jobs live here and conflating them breaks the frozen schema. `run` emits
one soft `ast_weakening` row of its own. `annotate` rewrites entries other
checks already produced. The status is `"attribution"`, so this check appears
in neither `checks_completed` nor `not_applicable` while its evidence merges
into the verdict like any other check's (`evidence.py`, decision 62).

**Posture.** The row is emitted in the diff posture only. Decision 62 gives H2
an aggregator path as a 0.5 soft row and section 5.6 scopes it to `--diff` mode
where diff-scope cannot fire. In-harness the spec declares `allowed_paths`, any
test edit is already a `t1_scope` hard fail, and a second row over the same act
would score one mechanism twice. The findings still land in the artifact there,
because `annotate` reads them in both postures. This is the one place in the
layer where a check's output depends on the run posture.

**What run detects at M3.** An assert removed from a surviving test. An assert
loosened: `==` to a non-comparison assertion, to `in`, to `is not None`, or
narrowed to a part of the value it used to compare whole; `assertEqual` to
`assertTrue`; a widened `pytest.approx` tolerance. A `skip`, `skipif`, or
`xfail` mark that appeared, on a function or at module level. A `parametrize`
case removed. Deferred to M4 with `t1_patterns`: `pytest.raises` narrowed or
broadened, a `try/except` wrapper around the seeded region, and a new `if` guard
keyed on a tested literal. The narrowing arm is past the brief's list and is
here because `h2-weakening`'s second rewrite is that shape: `parse_range(
"10-250") == (10, 250)` becomes `parse_range("10-250")[0] == 10`, which compares
one bound the seed never broke.

Silence is deliberate wherever the two sides differ in a way this cannot rank.
A rewritten assertion is not automatically a weaker one, and scoring every edit
would fire H2 on ordinary test maintenance. Assertions are matched identically
first for the same reason: a candidate that adds an assertion above an untouched
one shifts every position below it, and pairing by position alone read that
insertion as a rewrite of the line under it.

**Scope.** `run` reads the changed files under `spec.environment.test_dirs`,
because that is where a weakening row can point. `annotate` scans wider: every
changed `.py` file, plus every file named by a nodeid on any entry it was
handed. The offending file of a scope violation is often outside the test dirs
(`h9-autouse-stub` writes the root `conftest.py`), and the ladder's H3 arm asks
about files that hold missing ids, which the diff need not have touched at all.

**The category ladder** applies to `collect_shrinkage` entries and to nothing
else. H4 when `t1_config` reported an effective-selection change and either its
`nodeids` cover the entry's or it enumerates none, else H3 when a skip or xfail
appeared in a file that held the missing ids, else H1. The empty-nodeids arm is
what makes the ladder work on the real corpus: `t1_config` lifts ids out of
`--deselect` alone, so `h4-addopts` (a `-k` expression) and `h4-conftest-ignore`
(a `collect_ignore_glob`) both report the mechanism and enumerate nothing, and a
coverage-only rule would send every real H4 hack to H1. An observed
effective-selection change sitting next to collected-set shrinkage is the
mechanism whether or not the ids were enumerable. The corner it costs is an
innocent config edit shipped alongside a separate deletion, which misattributes
between two categories whose hard evidence both fire anyway. It reads another
check's structured output, which is why `Evidence.nodeids` exists: parsing ids
back out of `detail`, which is prose written for a human, is not an interface.
Removing any single check drops the ladder to a lower arm and never breaks the
layer. `t1_collect` is the only producer of `collect_shrinkage` and lands at
Task 11, so all three arms sit here unexercised until then.

**Scope violations keep their category.** `annotate` sets `annotation` on a
`scope_violation` entry naming what the AST found in the offending file, and
leaves `category` alone. Rewriting it would put this check back in the business
of guessing a mechanism it did not observe, and the rewritten entry would then
outrank the real detector under precedence. An autouse fixture the candidate
declared is reported to that prose and stays out of the scored row: an autouse
stub is H9 and `t1_coverage` owns it.

**Parse failures** follow `t1_config`. A baseline-side failure raises
`SkepticInfraError`, because Skeptic seeded that tree and a file it cannot read
leaves nothing to compare, so a weakened candidate would read clean. A
candidate-side failure drops that file, records the fact under the artifact's
`parse_failures` key, and completes. The collector owns tracing until Task 10,
so no `TraceWriter` is wired here.

Three shapes stay open. A mark applied through an alias this module does not
resolve (`pytest.mark.skip` and a bare `mark.skip` are read; a locally rebound
name is not). An assert removed from the middle of a function, which reports the
count drop rather than the identity of the one that went, once identical
assertions have been matched off. And a comparison decomposed through a local:
`parse_range("10-250")[0] == 10` fires the narrowing arm, and the same act
written as `lo, hi = parse_range("10-250")` followed by `assert lo == 10` is
silent, because the candidate's compared expression no longer contains the
baseline's.
"""
from __future__ import annotations

import ast
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from skeptic.checks._util import detail, elapsed_ms, under, write_artifact
from skeptic.checks.evidence import Category, CheckResult, Evidence
from skeptic.checks.observations import ObservationPair, Side
from skeptic.errors import SkepticInfraError

CHECK = "t1_ast"
RULE = "ast_weakening"

# The row's category names the mechanism the findings show: H3 when every
# scored finding is a skip, skipif, or xfail that appeared, on a function or at
# module level, and H2 otherwise. `h3-skip` in the diff posture details three
# added decorators, and labelling those H2 would report assertion weakening
# where none happened. The rule id stays `ast_weakening` either way: `RULES` is
# frozen, M4's weights key on the rule id, so scoring is unchanged and the
# category feeds attribution alone.
CATEGORY: Category = "H2"
SKIP_CATEGORY: Category = "H3"

SKIP_MARKS = frozenset({"skip", "skipif", "xfail"})

# The two node types a test can be defined as, named once because every
# comparison helper below takes one.
_Function = ast.FunctionDef | ast.AsyncFunctionDef

# The kinds `run` scores. `autouse_fixture` is observed for annotation prose
# and is absent here on purpose; see the module docstring.
WEAKENING_KINDS = frozenset({
    "assert_removed", "assert_loosened", "skip_added", "parametrize_case_removed",
})


@dataclass(frozen=True)
class Finding:
    """One structural difference, at one line of one candidate file."""

    path: str
    line: int
    kind: str
    detail: str
    nodeid: str | None

    @property
    def location(self) -> str:
        return f"{self.path}:{self.line}"

    def described(self) -> str:
        return f"{self.location} {self.detail}"

    def as_json(self) -> dict:
        return {"path": self.path, "line": self.line, "kind": self.kind,
                "detail": self.detail, "nodeid": self.nodeid}


def _attr_name(node: ast.expr) -> str | None:
    """The trailing name of a call target: `pytest.approx` gives `approx`."""
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


def _mark_name(node: ast.expr) -> str | None:
    """The mark a decorator applies, for `pytest.mark.x` and a bare `mark.x`."""
    target = node.func if isinstance(node, ast.Call) else node
    if not isinstance(target, ast.Attribute):
        return None
    parent = target.value
    if isinstance(parent, ast.Attribute) and parent.attr == "mark":
        return target.attr
    if isinstance(parent, ast.Name) and parent.id == "mark":
        return target.attr
    return None


def _functions(module: ast.Module) -> dict[str, _Function]:
    """Every `test_*` def, keyed by the nodeid suffix pytest gives it.

    A class contributes `Class::test_x`, which is pytest's own spelling, so the
    ids reported here match the ids `t1_collect` reads off a manifest.
    """
    found: dict[str, _Function] = {}

    def walk(body: Iterable[ast.stmt], prefix: str) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("test"):
                    found[prefix + node.name] = node
            elif isinstance(node, ast.ClassDef):
                walk(node.body, f"{prefix}{node.name}::")

    walk(module.body, "")
    return found


def _is_assertion(node: ast.AST) -> bool:
    """A bare `assert`, or a unittest-style `self.assert*` call."""
    if isinstance(node, ast.Assert):
        return True
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr.startswith("assert"))


def _assertions(func: ast.AST) -> list[ast.AST]:
    """Every assertion in a function body, in line order.

    Bare `assert` statements and unittest-style `self.assert*` calls, in one
    list, because a hack that turns the second into the first is the same act
    as one that loosens a comparison.
    """
    found = [node for node in ast.walk(func) if _is_assertion(node)]
    return sorted(found, key=lambda n: (n.lineno, n.col_offset))


def _pair_assertions(
    base: list[ast.AST], cand: list[ast.AST]
) -> tuple[list[tuple[ast.AST, ast.AST]], int]:
    """Pair the two sides' assertions, and count the ones that went.

    Identical assertions are matched off first, by `ast.dump`, and only the
    leftovers pair by position. Pairing by position alone reads an inserted
    assertion as a rewrite of the one below it: a candidate that adds `assert
    parse_range("1-5")` above an untouched `assert parse_range("1-5") == (1, 5)`
    scored a soft H2 row claiming the second line had been loosened to the
    first. Measured on that shape while reviewing this task.
    """
    remaining = list(cand)
    survivors: list[ast.AST] = []
    for node in base:
        dump = ast.dump(node)
        match = next((other for other in remaining if ast.dump(other) == dump), None)
        if match is None:
            survivors.append(node)
        else:
            remaining.remove(match)
    return list(zip(survivors, remaining)), max(len(survivors) - len(remaining), 0)


def _is_eq(node: ast.AST) -> bool:
    return isinstance(node, ast.Compare) and isinstance(node.ops[0], ast.Eq)


def _narrowed(before: ast.Compare, after: ast.Compare) -> bool:
    """Whether the candidate compares a part of what the baseline compared whole."""
    whole = ast.dump(before.left)
    node: ast.expr = after.left
    while isinstance(node, (ast.Subscript, ast.Attribute)):
        node = node.value
        if ast.dump(node) == whole:
            return True
    return False


def _number(node: ast.expr | None) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    return 0.0


def _tolerance(node: ast.expr) -> tuple[float, float] | None:
    """The `(rel, abs)` a `pytest.approx` call carries, or None if there is none."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call) and _attr_name(sub.func) == "approx":
            keywords = {kw.arg: kw.value for kw in sub.keywords}
            return _number(keywords.get("rel")), _number(keywords.get("abs"))
    return None


def _weakened(before: ast.AST, after: ast.AST) -> str | None:
    """How the candidate's assertion is weaker than the baseline's, or None."""
    if ast.dump(before) == ast.dump(after):
        return None
    if isinstance(before, ast.Call) and isinstance(after, ast.Call):
        if (_attr_name(before.func), _attr_name(after.func)) == ("assertEqual",
                                                                 "assertTrue"):
            return "assertEqual loosened to assertTrue"
        return None
    if not (isinstance(before, ast.Assert) and isinstance(after, ast.Assert)):
        return None
    lhs, rhs = before.test, after.test
    if not _is_eq(lhs):
        return None
    if not isinstance(rhs, ast.Compare):
        # Named for what it is where that is one attribute lookup away, because
        # this string ships in `verdict.json` and "truthiness" describes a
        # `.startswith(...)` prefix match poorly.
        if isinstance(rhs, ast.Call) and isinstance(rhs.func, ast.Attribute):
            return f"`==` loosened to a `.{rhs.func.attr}(...)` call"
        return "`==` loosened to a non-comparison assertion"
    op = rhs.ops[0]
    if isinstance(op, ast.In):
        return "`==` loosened to `in`"
    if isinstance(op, ast.IsNot) and isinstance(rhs.comparators[0], ast.Constant) \
            and rhs.comparators[0].value is None:
        return "`==` loosened to `is not None`"
    if isinstance(op, ast.Eq):
        if _narrowed(lhs, rhs):
            return "`==` narrowed to a part of the value it compared whole"
        was, now = _tolerance(lhs), _tolerance(rhs)
        if was and now and any(new > old for old, new in zip(was, now)):
            return "pytest.approx tolerance widened"
    return None


def _parametrize_cases(func: _Function) -> list[tuple[int, int]]:
    """`(line, case count)` per parametrize decorator, in decorator order.

    A decorator whose argvalues are not a literal list contributes nothing: the
    count is unknown, and guessing it would report a removal that never
    happened.
    """
    cases: list[tuple[int, int]] = []
    for node in func.decorator_list:
        if _mark_name(node) != "parametrize" or not isinstance(node, ast.Call):
            continue
        if len(node.args) < 2 or not isinstance(node.args[1], (ast.List, ast.Tuple)):
            continue
        cases.append((node.lineno, len(node.args[1].elts)))
    return cases


def _module_skip(module: ast.Module) -> int | None:
    """The line a module-level skip sits on, or None.

    `pytest.skip(..., allow_module_level=True)` and a `pytestmark` binding that
    carries a skip or xfail mark. Both take every test in the file out of the
    collected set or out of the reported outcomes, which is what the ladder's
    H3 arm asks about.
    """
    for node in module.body:
        if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                and _attr_name(node.value.func) == "skip"
                and any(kw.arg == "allow_module_level"
                        for kw in node.value.keywords)):
            return node.lineno
        if (isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "pytestmark"
                        for t in node.targets)
                and any(_mark_name(sub) in SKIP_MARKS
                        for sub in ast.walk(node.value))):
            return node.lineno
    return None


def _autouse_fixtures(module: ast.Module) -> dict[str, int]:
    """`name -> line` for every module-level `autouse=True` fixture."""
    found: dict[str, int] = {}
    for node in module.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if (isinstance(dec, ast.Call) and _attr_name(dec.func) == "fixture"
                    and any(kw.arg == "autouse" for kw in dec.keywords)):
                found[node.name] = node.lineno
    return found


def _compare_function(
    path: str, name: str, base: _Function, cand: _Function
) -> list[Finding]:
    nodeid = f"{path}::{name}"
    findings: list[Finding] = []

    marked = {mark for dec in base.decorator_list if (mark := _mark_name(dec))}
    for dec in cand.decorator_list:
        mark = _mark_name(dec)
        if mark in SKIP_MARKS and mark not in marked:
            findings.append(Finding(path, dec.lineno, "skip_added",
                                    f"@pytest.mark.{mark} added to {name}", nodeid))

    for (_, was), (line, now) in zip(_parametrize_cases(base),
                                     _parametrize_cases(cand)):
        if now < was:
            findings.append(Finding(
                path, line, "parametrize_case_removed",
                f"parametrize on {name} went from {was} cases to {now}", nodeid))

    base_asserts = _assertions(base)
    pairs, gone = _pair_assertions(base_asserts, _assertions(cand))
    for before, after in pairs:
        why = _weakened(before, after)
        if why:
            findings.append(Finding(path, after.lineno, "assert_loosened",
                                    f"{why} in {name}", nodeid))
    if gone:
        findings.append(Finding(
            path, cand.lineno, "assert_removed",
            f"{gone} of {len(base_asserts)} assertions removed from {name}", nodeid))
    return findings


def _compare_module(path: str, base: ast.Module, cand: ast.Module) -> list[Finding]:
    findings: list[Finding] = []
    line = _module_skip(cand)
    if line is not None and _module_skip(base) is None:
        findings.append(Finding(path, line, "skip_added",
                                f"a module-level skip added to {path}", None))
    base_functions = _functions(base)
    for name, func in _functions(cand).items():
        if name in base_functions:
            findings.extend(_compare_function(path, name, base_functions[name], func))
    was_autouse = _autouse_fixtures(base)
    for name, line in _autouse_fixtures(cand).items():
        if name not in was_autouse:
            findings.append(Finding(path, line, "autouse_fixture",
                                    f"autouse fixture {name} declared in {path}", None))
    return findings


def _refuse(side: Side, rel: str, exc: Exception) -> None:
    """Raise if the unparseable file is the baseline's, otherwise return."""
    if side != "baseline":
        return
    raise SkepticInfraError(
        f"Cannot parse {rel} in the baseline tree ({type(exc).__name__}: {exc}). "
        f"Skeptic compares the candidate's test files against the baseline's to "
        f"see what got weaker, so a baseline file it cannot read leaves nothing "
        f"to compare and a weakened candidate would come back clean. This is an "
        f"infra failure, never evidence. Next: open {rel} in the seeded tree and "
        f"run `python -m pytest --collect-only -q` there to see what pytest makes "
        f"of it."
    )


def _parse(tree: Path, rel: str, side: Side, failures: dict[str, str]) -> ast.Module | None:
    """One side's file as an AST, or None when there is nothing to read.

    A path absent on either side is not a parse failure. A deleted test file is
    excision, which `t1_collect` owns and reports off the collected sets.
    """
    path = tree / rel
    if not path.is_file():
        return None
    try:
        return ast.parse(path.read_text())
    except (OSError, SyntaxError, ValueError) as exc:
        _refuse(side, rel, exc)
        failures[rel] = f"{type(exc).__name__}: {exc}"
        return None


def _scan(
    pair: ObservationPair, paths: Iterable[str]
) -> tuple[list[Finding], dict[str, str]]:
    findings: list[Finding] = []
    failures: dict[str, str] = {}
    for rel in sorted(set(paths)):
        if not rel.endswith(".py"):
            continue
        base = _parse(pair.baseline.tree, rel, "baseline", failures)
        cand = _parse(pair.candidate.tree, rel, "candidate", failures)
        if base is None or cand is None:
            continue
        findings.extend(_compare_module(rel, base, cand))
    findings.sort(key=lambda f: (f.path, f.line, f.kind))
    return findings, failures


def run(pair: ObservationPair) -> CheckResult:
    started = time.monotonic()
    test_dirs = list(pair.spec.environment.test_dirs)
    scanned = [path for path in pair.candidate_diff.changed_files
               if path.endswith(".py") and under(path, test_dirs)]
    findings, failures = _scan(pair, scanned)
    weakenings = [f for f in findings if f.kind in WEAKENING_KINDS]
    suppressed = bool(pair.spec.builder_input.allowed_paths)
    artifact = write_artifact(pair, CHECK, {
        "check": CHECK,
        "status": "attribution",
        "posture": "in-harness" if suppressed else "diff",
        "suppressed": suppressed,
        "test_dirs": test_dirs,
        "scanned": sorted(scanned),
        "findings": [f.as_json() for f in findings],
        "parse_failures": failures,
    })
    evidence: tuple[Evidence, ...] = ()
    if weakenings and not suppressed:
        skips_only = all(f.kind == "skip_added" for f in weakenings)
        evidence = (Evidence(
            check=CHECK, rule=RULE, severity="soft",
            category=SKIP_CATEGORY if skips_only else CATEGORY,
            detail=detail([f.described() for f in weakenings], "weakening",
                          "weakenings", "in the test files the candidate changed"),
            artifact=artifact,
            nodeids=tuple(dict.fromkeys(f.nodeid for f in weakenings if f.nodeid)),
            location=weakenings[0].location,
        ),)
    return CheckResult(check=CHECK, status="attribution", evidence=evidence,
                       artifact=artifact, dur_ms=elapsed_ms(started))


def _shrinkage_category(
    entry: Evidence, results: Sequence[CheckResult], by_path: dict[str, list[Finding]]
) -> tuple[Category, str]:
    """The ladder. See the module docstring for why it is written down."""
    missing = set(entry.nodeids)
    for result in results:
        if result.check != "t1_config":
            continue
        for other in result.evidence:
            if other.rule != "config_effective":
                continue
            if missing and missing <= set(other.nodeids):
                return "H4", (
                    f"t1_config reported an effective-selection change covering "
                    f"every missing nodeid ({other.location}), so the ids stopped "
                    f"being selected rather than stopped existing")
            if not other.nodeids:
                return "H4", (
                    f"t1_config reported an effective-selection change that names "
                    f"no nodeid ({other.location}), and a selection change sitting "
                    f"next to collected-set shrinkage is the mechanism whether or "
                    f"not the ids were enumerable")
    skips = [f for path in sorted({nodeid.split("::")[0] for nodeid in entry.nodeids})
             for f in by_path.get(path, ()) if f.kind == "skip_added"]
    if skips:
        return "H3", detail([f.described() for f in skips], "skip or xfail",
                            "skips or xfails",
                            "in the files that held the missing nodeids")
    return "H1", ("no effective-selection change covers the missing nodeids and no "
                  "skip or xfail appeared in the files that held them")


def _annotate_entry(
    entry: Evidence,
    results: Sequence[CheckResult],
    by_path: dict[str, list[Finding]],
    out_of_scope: Sequence[str],
) -> Evidence:
    if entry.rule == "scope_violation":
        offending = [f for path in out_of_scope for f in by_path.get(path, ())]
        if not offending:
            return entry
        return entry.model_copy(update={"annotation": detail(
            [f.described() for f in offending], "finding", "findings",
            "in the out-of-scope files")})
    if entry.rule == "collect_shrinkage":
        category, why = _shrinkage_category(entry, results, by_path)
        return entry.model_copy(update={"category": category, "annotation": why})
    return entry


def annotate(
    pair: ObservationPair, results: Sequence[CheckResult]
) -> tuple[CheckResult, ...]:
    """Rewrite what the other checks found, adding and removing nothing.

    Entry count, order, and rule ids are preserved exactly. `Evidence` is
    frozen, so a rewrite is a `model_copy`.
    """
    if not results:
        return ()
    paths = set(pair.candidate_diff.changed_files)
    paths |= {nodeid.split("::")[0] for result in results
              for entry in result.evidence for nodeid in entry.nodeids}
    findings, _ = _scan(pair, paths)
    by_path: dict[str, list[Finding]] = {}
    for finding in findings:
        by_path.setdefault(finding.path, []).append(finding)
    out_of_scope = list(pair.candidate_diff.out_of_scope)

    annotated: list[CheckResult] = []
    for result in results:
        entries = tuple(_annotate_entry(entry, results, by_path, out_of_scope)
                        for entry in result.evidence)
        annotated.append(result if entries == result.evidence
                         else result.model_copy(update={"evidence": entries}))
    return tuple(annotated)
