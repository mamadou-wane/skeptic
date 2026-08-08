"""Mutant generation and deterministic stratified sampling.

Pure: a function of `ObservationPair` (the candidate tree, the diff, the
spec) plus config. No container, no subprocess, no daemon, no `Date`/time/
randomness outside `random.Random(seed)`. Task 9 executes what this module
produces; nothing here runs a mutant.

**The operator set.** Six entries. The engineering plan's own list names
seven; `off_by_one` folds the plan's separate "constant tweak" entry into
itself, since both are "integer constant n -> n + 1" and a second operator
for the same transformation would double-count one mutation as two rule ids
(DECISIONS row 109). `conditional_boundary` and `conditional_negation` both
work on `ast.Compare.ops`, one flip map each, with one application per
qualifying position in a chained comparison: `a < b < c` has two comparison
operators and yields two independent `conditional_boundary` mutants.

**off_by_one and a negative constant.** `-1` parses as
`UnaryOp(USub(), Constant(1))`, never as a `Constant(-1)`: Python's own
parser does not fold the sign into the literal. The operator targets
`ast.Constant` nodes directly (`int`, excluding `bool`, since `bool` is an
`int` subclass and `True + 1` is not the mutation this operator names), so a
constant reached through a leading unary minus is still found by `ast.walk`
and still incremented, with no special case: `-1` becomes `-2`, the sign's
own operand growing by one before the minus is reapplied. This is the
narrowest reading of "integer constant n -> n + 1": the transformation acts
on the constant node's stored value, never on the number the source text
appears to spell with a sign attached. Pinned by
`test_off_by_one_increments_the_stored_literal_through_a_leading_unary_minus`.

**arithmetic_swap and `**`.** Only the four operators the plan spells out,
`+ <-> -` and `* <-> //`, are touched. `ast.Pow` is not in the flip table,
so `a ** b` produces no `arithmetic_swap` mutant; the operator's own name
lists exactly two swapped pairs, and `**` has no obvious inverse to swap it
with, so widening the table would be inventing a seventh transformation the
brief never asked for. Pinned by
`test_arithmetic_swap_does_not_touch_the_power_operator`. `ast.AugAssign`
(`x += 1`) is out of scope on the same narrow reading: the brief's shape is
a `BinOp` expression, and an augmented assignment's target is a store
statement, a different node entirely, so folding it in would be a second,
unrequested application site.

**call_removal.** A standalone expression-statement call is
`ast.Expr(value=ast.Call(...))` sitting directly in a statement list: a
function, class, or module body, an `if`/`elif`/`else` arm, a `for`/`while`
body, a `try` body or an `except`/`finally` block, at any nesting depth.
The mutation deletes that one statement from its parent's list. Deleting the
only statement in a block leaves an empty body, which `ast.unparse` renders
as a header line with nothing indented under it; `compile` then raises
`IndentationError`, caught like any other invalid mutant (`valid=False`,
`mutated_source=""`). No special case is needed for "would this empty a
block": the compile step already answers it.

**The caller scan is a documented approximation.** `caller_function_spans`
finds a function containing an `ast.Call` whose callee name, direct
(`ast.Name`) or attribute (`ast.Attribute.attr`), equals a changed
function's name. It does not resolve imports or bind `self`/instance
types, so a same-name method on an unrelated class over-includes (the
method never calls the changed function at all, only one that shares its
name) and a changed function imported under an alias under-includes (the
alias never matches the original name). Both are bounded by the caller
row's 0.25 weight (decision 4's sibling in the plan); import-graph
precision was cut at the v2 gate. Pinned deliberately:
`test_caller_scan_overincludes_a_same_name_method_and_that_is_pinned` locks
in the false positive, so a future change to the matching rule shows as a
visible diff instead of a silent behavior change. Caller scanning is scoped to
`src_dirs`, excluding `test_dirs` and `conftest.py` the same way changed
spans are: a test file calling a changed function is not source whose
callers matter to mutation testing. A function that is itself a changed
function is never also counted as its own caller, which matters for direct
recursion.

**Sampling and "enclosing function."** `Mutant` carries `function`, the
enclosing function's qualified dotted name (`""` at module level), built by
`_enclosing_function_map`: a dedicated recursive walk, since `ast.walk`'s
breadth-first order carries no parent information. `sample_mutants`'s
stratum key is `(path, function, operator)`, matching the sampling contract
exactly. An earlier version of this module keyed strata on the mutant's own
`line` instead, reasoning that `Mutant`'s brief-fixed shape carried no
function-identity field; a review measured that substitution directly and
found it starves compact functions (DECISIONS row 109(c), corrected by row
110): a two-line function sitting beside a many-line one received zero
mutants through budget 8 under the line key, because the sprawling
function's many single-mutant line-strata dominate every round-robin pass,
while the function key gives the compact function both of its mutants by
budget 2. `function` is now a real field for exactly that reason.

**The caller pass excludes nodes a changed span already covers.**
`caller_function_spans`'s own exclusion (a caller candidate whose span
exactly equals one of `changed`'s entries is dropped) only catches a
function reported as a caller in its own right; it misses a caller
function nested inside an already-changed enclosing function, and misses a
changed function nested inside an already-caller-eligible enclosing
function, since neither nested function's own span is the one excluded.
`generate_mutants` closes both shapes at the mutation-generation step
instead of the span-computation step: when building caller-population
mutants for a path, every node whose line falls inside that same path's
changed spans is skipped, regardless of which caller function span
nominally contains it. Skipping there rather than resolving it in
`caller_function_spans` keeps the fix node-level, which is what makes it
symmetric across both nesting directions. Without it, the same
`(path, line, operator)` triple could be generated once under each
population, and since `_mutant_id` hashes exactly that triple plus a
per-call occurrence counter, the two mutants collide on one id (DECISIONS
row 110).

**Unparseable source is an infra failure.** All four
`ast.parse` call sites (changed-span computation, caller-name recovery, the
caller scan itself, and mutant generation) go through `_parse_file`, which
raises `SkepticInfraError` naming the file on `SyntaxError` or `ValueError`.
A file this module cannot parse and a file with no mutable code in it would
otherwise both silently produce zero mutants, and the two mean very
different things about whether Skeptic can stand behind the resulting
kill rate.

**Determinism.** `sample_mutants` groups by `(0 if changed else 1, path,
function, operator)`, shuffles each group once with a `random.Random(seed)`
advanced in sorted-key order (so the sequence of shuffle calls never
depends on the input list's own order), then round-robins across the
sorted keys taking one mutant per stratum per pass until `budget` is
reached or every stratum is empty. The per-pass shape matters: it is what
makes a tight budget spend across every changed stratum once before
returning to any of them a second time, rather than exhausting one
stratum's whole pool before moving to the next.
"""
from __future__ import annotations

import ast
import copy
import hashlib
import random
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from skeptic.checks._util import under
from skeptic.checks.observations import CoverageReport, ObservationPair, parse_unified_diff
from skeptic.errors import SkepticInfraError

# The selection Task 9's batch runner uses for a caller-population mutant: the
# coverage report is scoped to the candidate's changed files by design (row
# 72's sibling decision), so a caller line carries no per-line context to
# select from, and the fallback is the full uninstrumented suite. Both
# admitted corpus suites are cheap uninstrumented (click ~2.8s, rich ~3.4s
# worst 3.95s; docs/admission), cheaper than widening the instrumented report
# to cover caller files too (DECISIONS row 113).
FULL_SUITE: tuple[str, ...] = ("<full-suite>",)

OPERATORS: tuple[str, ...] = (
    "conditional_boundary",    # < <-> <=, > <-> >=
    "conditional_negation",    # invert a comparison: == <-> !=, is <-> is not
    "off_by_one",              # integer constant n -> n + 1 (constant tweak folded here)
    "arithmetic_swap",         # + <-> -, * <-> //
    "return_substitution",     # return <expr> -> return None
    "call_removal",            # a standalone expression-statement call removed
)

CONFTEST = "conftest.py"


@dataclass(frozen=True)
class Mutant:
    mutant_id: str                       # 12-hex content hash of (path, line, operator, occurrence)
    path: str
    line: int                            # ORIGINAL candidate-tree line, the coverage lookup key
    operator: str
    function: str                        # enclosing function's qualified name; "" at module level
    population: Literal["changed", "caller"]
    mutated_source: str                  # full post-mutation file text (ast.unparse), or "" when invalid
    valid: bool                          # compile() succeeded at generation time


def _line_overlaps(span: tuple[int, int], ranges: tuple[tuple[int, int], ...]) -> bool:
    start, end = span
    return any(start <= r_end and r_start <= end for r_start, r_end in ranges)


def _parse_file(tree: Path, rel: str) -> ast.Module:
    """Parse `rel` out of `tree`, or raise loudly.

    Every `ast.parse` call in this module goes through here, so an
    unparseable file fails the same way regardless of which of the four call
    sites (changed spans, caller-name recovery, the caller scan, mutant
    generation) reached it, instead of quietly returning zero mutants, which
    looks identical to a file with no mutable code in it.
    """
    path = tree / rel
    try:
        return ast.parse(path.read_text())
    except (SyntaxError, ValueError) as exc:
        raise SkepticInfraError(
            f"Cannot parse {rel} ({type(exc).__name__}: {exc}). Mutation "
            f"generation reads the candidate's own source to find operator "
            f"application sites and enclosing functions, so a file it cannot "
            f"parse leaves nothing to mutate, and reading that silently as "
            f"zero mutants would look identical to a file with no mutable "
            f"code in it. This is an infra failure, never evidence. Next: "
            f"open {path} and run `python -m py_compile {path}` to see what "
            f"the parser rejects."
        ) from exc


def _enclosing_function_map(module: ast.Module) -> dict[int, str]:
    """`id(node) -> qualified enclosing function name`, `""` at module level.

    A dedicated recursive walk, since `ast.walk`'s breadth-first order
    carries no parent information. A class contributes a name segment (so a
    method's nodes map to `"Class.method"`) without itself counting as a
    function; only entering a `FunctionDef`/`AsyncFunctionDef` changes which
    function a node's descendants belong to.
    """
    mapping: dict[int, str] = {}

    def walk(node: ast.AST, prefix: str, enclosing: str) -> None:
        mapping[id(node)] = enclosing
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualified = f"{prefix}{child.name}"
                walk(child, f"{qualified}.", qualified)
            elif isinstance(child, ast.ClassDef):
                walk(child, f"{prefix}{child.name}.", enclosing)
            else:
                walk(child, prefix, enclosing)

    walk(module, "", "")
    return mapping


def _function_spans(module: ast.Module) -> list[tuple[int, int]]:
    """Every `FunctionDef`/`AsyncFunctionDef` span, nested defs included."""
    return [
        (node.lineno, node.end_lineno or node.lineno)
        for node in ast.walk(module)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def changed_function_spans(pair: ObservationPair) -> dict[str, tuple[tuple[int, int], ...]]:
    """Changed functions: spans whose lines intersect the diff's candidate-side ranges.

    Excludes files under `test_dirs` and any `conftest.py`, mirroring
    `t1_coverage`'s cuts: a mutant measures whether the patch's own source is
    exercised, and a test file is the exerciser rather than the exercised.
    """
    diff_spans = parse_unified_diff(pair.candidate_diff.diff_path.read_text())
    test_dirs = list(pair.spec.environment.test_dirs)
    result: dict[str, tuple[tuple[int, int], ...]] = {}
    for path, ranges in sorted(diff_spans.items()):
        if not path.endswith(".py") or not ranges:
            continue
        if under(path, test_dirs) or Path(path).name == CONFTEST:
            continue
        module = _parse_file(pair.candidate.tree, path)
        spans = tuple(sorted(
            span for span in _function_spans(module) if _line_overlaps(span, ranges)
        ))
        if spans:
            result[path] = spans
    return result


def _changed_function_names(
    pair: ObservationPair, changed: dict[str, tuple[tuple[int, int], ...]]
) -> set[str]:
    """Re-derive the changed functions' own names from their spans.

    `changed` carries spans, matching `changed_function_spans`'s own return
    shape, which has no name field, so the name a caller scan needs is
    recovered by re-parsing each changed file and matching span to span.
    """
    names: set[str] = set()
    for path, spans in changed.items():
        module = _parse_file(pair.candidate.tree, path)
        wanted = set(spans)
        for node in ast.walk(module):
            if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and (node.lineno, node.end_lineno or node.lineno) in wanted):
                names.add(node.name)
    return names


def _src_py_files(tree: Path, src_dirs: list[str]) -> set[str]:
    found: set[str] = set()
    for src in src_dirs:
        stripped = src.rstrip("/")
        root = tree if stripped in ("", ".") else tree / stripped
        if not root.is_dir():
            continue
        for p in root.rglob("*.py"):
            found.add(p.relative_to(tree).as_posix())
    return found


def _calls_any(node: ast.AST, names: set[str]) -> bool:
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        func = sub.func
        if isinstance(func, ast.Name) and func.id in names:
            return True
        if isinstance(func, ast.Attribute) and func.attr in names:
            return True
    return False


def caller_function_spans(
    pair: ObservationPair, changed: dict[str, tuple[tuple[int, int], ...]]
) -> dict[str, tuple[tuple[int, int], ...]]:
    """Functions in `src_dirs` `.py` files that call a changed function by name.

    `patch_only` scope, or no changed function at all, yields nothing.
    Matching is name-based, direct (`f(...)`) or attribute (`obj.f(...)`)
    form; see the module docstring for the two failure shapes this accepts.
    A function that is itself one of `changed`'s spans is never also
    reported as a caller.
    """
    if pair.spec.verification.mutation.scope != "patch_plus_callers" or not changed:
        return {}
    names = _changed_function_names(pair, changed)
    if not names:
        return {}
    test_dirs = list(pair.spec.environment.test_dirs)
    src_dirs = list(pair.spec.environment.src_dirs)
    result: dict[str, tuple[tuple[int, int], ...]] = {}
    for path in sorted(_src_py_files(pair.candidate.tree, src_dirs)):
        if under(path, test_dirs) or Path(path).name == CONFTEST:
            continue
        module = _parse_file(pair.candidate.tree, path)
        own_changed = set(changed.get(path, ()))
        spans = tuple(sorted(
            (node.lineno, node.end_lineno or node.lineno)
            for node in ast.walk(module)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and (node.lineno, node.end_lineno or node.lineno) not in own_changed
            and _calls_any(node, names)
        ))
        if spans:
            result[path] = spans
    return result


# --- Operator application tables -------------------------------------------
#
# Each non-`call_removal` operator is a (matcher, applier) pair. `matcher`
# takes one AST node and returns the list of slot indices it can transform
# (usually `[0]` for "this whole node", or one index per qualifying
# comparison operator for the two `Compare`-based operators). `applier`
# mutates the node in place at that slot.

_BOUNDARY_FLIP: dict[type, type] = {
    ast.Lt: ast.LtE, ast.LtE: ast.Lt, ast.Gt: ast.GtE, ast.GtE: ast.Gt,
}
_NEGATION_FLIP: dict[type, type] = {
    ast.Eq: ast.NotEq, ast.NotEq: ast.Eq, ast.Is: ast.IsNot, ast.IsNot: ast.Is,
}
_ARITHMETIC_FLIP: dict[type, type] = {
    ast.Add: ast.Sub, ast.Sub: ast.Add, ast.Mult: ast.FloorDiv, ast.FloorDiv: ast.Mult,
}


def _compare_slots(node: ast.AST, flip: dict[type, type]) -> list[int]:
    if not isinstance(node, ast.Compare):
        return []
    return [i for i, op in enumerate(node.ops) if type(op) in flip]


def _make_compare_applier(flip: dict[type, type]):
    def apply(node: ast.Compare, slot: int) -> None:
        node.ops[slot] = flip[type(node.ops[slot])]()
    return apply


def _arithmetic_slots(node: ast.AST) -> list[int]:
    if isinstance(node, ast.BinOp) and type(node.op) in _ARITHMETIC_FLIP:
        return [0]
    return []


def _apply_arithmetic(node: ast.BinOp, slot: int) -> None:
    node.op = _ARITHMETIC_FLIP[type(node.op)]()


def _off_by_one_slots(node: ast.AST) -> list[int]:
    if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool):
        return [0]
    return []


def _apply_off_by_one(node: ast.Constant, slot: int) -> None:
    node.value = node.value + 1


def _return_slots(node: ast.AST) -> list[int]:
    is_none_constant = isinstance(node, ast.Return) and isinstance(node.value, ast.Constant) \
        and node.value.value is None
    if isinstance(node, ast.Return) and node.value is not None and not is_none_constant:
        return [0]
    return []


def _apply_return(node: ast.Return, slot: int) -> None:
    node.value = ast.Constant(value=None)


_OPERATOR_TABLE = {
    "conditional_boundary": (
        lambda node: _compare_slots(node, _BOUNDARY_FLIP), _make_compare_applier(_BOUNDARY_FLIP)),
    "conditional_negation": (
        lambda node: _compare_slots(node, _NEGATION_FLIP), _make_compare_applier(_NEGATION_FLIP)),
    "off_by_one": (_off_by_one_slots, _apply_off_by_one),
    "arithmetic_swap": (_arithmetic_slots, _apply_arithmetic),
    "return_substitution": (_return_slots, _apply_return),
}

_BODY_ATTRS = ("body", "orelse", "finalbody")


def _call_removal_sites(
    nodes: list[ast.AST], spans: tuple[tuple[int, int], ...]
) -> list[tuple[int, str, int, int]]:
    """`(parent index in nodes, body attr, position, line)` per standalone call."""
    sites: list[tuple[int, str, int, int]] = []
    for i, node in enumerate(nodes):
        for attr in _BODY_ATTRS:
            body = getattr(node, attr, None)
            if not isinstance(body, list):
                continue
            for pos, stmt in enumerate(body):
                if (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)
                        and _line_overlaps((stmt.lineno, stmt.lineno), spans)):
                    sites.append((i, attr, pos, stmt.lineno))
    return sites


def _mutant_id(path: str, line: int, operator: str, occurrence: int) -> str:
    payload = f"{path}:{line}:{operator}:{occurrence}".encode()
    return hashlib.sha256(payload).hexdigest()[:12]


def _finish(
    path: str, line: int, operator: str, occurrence: int, function: str,
    population: Literal["changed", "caller"], mutated_module: ast.Module,
) -> Mutant:
    mutant_id = _mutant_id(path, line, operator, occurrence)
    try:
        source = ast.unparse(mutated_module)
        compile(source, path, "exec")
    except (SyntaxError, ValueError):
        return Mutant(mutant_id=mutant_id, path=path, line=line, operator=operator,
                      function=function, population=population, mutated_source="", valid=False)
    return Mutant(mutant_id=mutant_id, path=path, line=line, operator=operator,
                  function=function, population=population, mutated_source=source, valid=True)


def _mutants_for_file(
    path: str, module: ast.Module, spans: tuple[tuple[int, int], ...],
    population: Literal["changed", "caller"],
    exclude: tuple[tuple[int, int], ...] = (),
) -> list[Mutant]:
    """Every operator application inside `spans`, skipping any line in `exclude`.

    `exclude` is the caller pass's guard against a node a changed span
    already covers (see the module docstring): the changed pass always
    calls this with `exclude=()`.
    """
    nodes = list(ast.walk(module))
    enclosing = _enclosing_function_map(module)
    occurrence: dict[tuple[int, str], int] = {}

    def _next_occurrence(line: int, operator: str) -> int:
        key = (line, operator)
        n = occurrence.get(key, 0)
        occurrence[key] = n + 1
        return n

    results: list[Mutant] = []
    for operator in OPERATORS:
        if operator == "call_removal":
            for parent_idx, attr, pos, line in _call_removal_sites(nodes, spans):
                if _line_overlaps((line, line), exclude):
                    continue
                occ = _next_occurrence(line, operator)
                function = enclosing[id(getattr(nodes[parent_idx], attr)[pos])]
                mutated = copy.deepcopy(module)
                parent = list(ast.walk(mutated))[parent_idx]
                del getattr(parent, attr)[pos]
                results.append(_finish(path, line, operator, occ, function, population, mutated))
            continue
        matcher, applier = _OPERATOR_TABLE[operator]
        for node_idx, node in enumerate(nodes):
            lineno = getattr(node, "lineno", None)
            if lineno is None or not _line_overlaps((lineno, lineno), spans):
                continue
            if _line_overlaps((lineno, lineno), exclude):
                continue
            for slot in matcher(node):
                occ = _next_occurrence(lineno, operator)
                function = enclosing[id(node)]
                mutated = copy.deepcopy(module)
                target = list(ast.walk(mutated))[node_idx]
                applier(target, slot)
                results.append(
                    _finish(path, lineno, operator, occ, function, population, mutated))
    return results


def generate_mutants(pair: ObservationPair) -> tuple[Mutant, ...]:
    """Every application of every operator over the changed and caller spans.

    Changed functions first, then callers (`patch_plus_callers` only; empty
    otherwise), each population sorted by path. `mutated_source` comes from
    `ast.unparse`ing a deep copy of the file's original tree with one
    operator applied at one site; `line` keeps that site's ORIGINAL lineno,
    which is why it is captured before the copy is unparsed. Every mutant is
    compiled at generation time; a failure is kept with `valid=False` rather
    than dropped.
    """
    changed = changed_function_spans(pair)
    callers = caller_function_spans(pair, changed)
    results: list[Mutant] = []
    for population, spans_by_path in (("changed", changed), ("caller", callers)):
        # The caller pass excludes any node a changed span for the same path
        # already covers, so a function nested either way across the two
        # populations is never mutated twice under one `(path, line,
        # operator)` triple. See the module docstring.
        for path in sorted(spans_by_path):
            module = _parse_file(pair.candidate.tree, path)
            exclude = changed.get(path, ()) if population == "caller" else ()
            results.extend(
                _mutants_for_file(path, module, spans_by_path[path], population, exclude))
    return tuple(results)


def sample_mutants(mutants: Sequence[Mutant], budget: int, seed: int) -> tuple[Mutant, ...]:
    """Stratified, seeded, budget-capped sample. See the module docstring's
    "Sampling and enclosing function" and "Determinism" sections for the
    stratum key and the round-robin's exact order."""
    grouped: dict[tuple[int, str, str, str], list[Mutant]] = {}
    for mutant in mutants:
        rank = 0 if mutant.population == "changed" else 1
        key = (rank, mutant.path, mutant.function, mutant.operator)
        grouped.setdefault(key, []).append(mutant)

    rng = random.Random(seed)
    ordered_keys = sorted(grouped)
    pools: dict[tuple[int, str, str, str], list[Mutant]] = {}
    for key in ordered_keys:
        pool = list(grouped[key])
        rng.shuffle(pool)
        pools[key] = pool

    result: list[Mutant] = []
    while len(result) < budget:
        took_any = False
        for key in ordered_keys:
            pool = pools[key]
            if not pool:
                continue
            result.append(pool.pop(0))
            took_any = True
            if len(result) >= budget:
                break
        if not took_any:
            break
    return tuple(result)


def _stripped_qualname(parts: list[str]) -> list[str]:
    """`::`-split nodeid parts with a `[param]` suffix stripped from the last.

    Pytest's parametrize suffix sits only on the final `::` part (the
    parametrized function or method name), so only that part is stripped.
    """
    if not parts:
        return parts
    head, last = parts[:-1], parts[-1]
    return [*head, last.split("[", 1)[0]]


def _path_segments(path: str) -> tuple[str, ...]:
    """`path`'s own dotted segments: every directory component, then the
    file's stem with `.py` stripped. `"tests/test_utils/test_x.py"` is
    `("tests", "test_utils", "test_x")`, the form a coverage context's module
    portion is some trailing run of.
    """
    segments = path.split("/")
    segments[-1] = Path(segments[-1]).stem
    return tuple(segments)


def _resolve_module(
    context_parts: tuple[str, ...], files: dict[str, tuple[str, ...]]
) -> tuple[str, int] | None:
    """The one collected file `context_parts` names, and how many of its
    leading segments the file's own path consumed as the module portion, or
    `None` if no collected file's path ends that way at any length.

    Longest run wins, checked from `len(context_parts) - 1` (leaving at least
    one segment for the qualname) down to 1. A package directory (one sitting
    under an `__init__.py`, coverage.py's own `dynamic_context = test_function`
    reads it off `sys.modules`) makes the context carry that directory as a
    leading dotted segment, one per package level between the test file and
    the first ancestor pytest did not import as a package; a flat test
    directory contributes none, and the module is just the file's bare stem.
    Trying the longest possible split first, rather than always taking one
    segment, is what lets one rule resolve both shapes without first knowing
    which one the repo uses: `test_minirepo.test_parse_range_basic` resolves
    at length 1 (no collected file ends in two segments matching a longer
    prefix), `test_utils.test_make_default_short_help.test_make_default_short_help`
    resolves at length 2 against `tests/test_utils/test_make_default_short_help.py`,
    and `tests.test_columns.test_render` resolves at length 2 against
    `tests/test_columns.py`.

    Raises `SkepticInfraError` if more than one collected file's path ends
    with the winning length's segments: a real naming collision (two test
    files sharing a stem, or a stem plus enclosing directories, at exactly
    the depth that would otherwise resolve), which this bridge refuses to
    break by guessing one.
    """
    for k in range(len(context_parts) - 1, 0, -1):
        prefix = context_parts[:k]
        winners = sorted(file for file, segments in files.items()
                         if len(segments) >= k and segments[-k:] == prefix)
        if len(winners) > 1:
            raise SkepticInfraError(
                f"Coverage context module prefix {'.'.join(prefix)!r} matches "
                f"more than one collected file: {winners}. Skeptic resolves a "
                f"dotted coverage context to one file by the longest leading "
                f"run of its dotted segments that equals the end of exactly "
                f"one collected file's own path, and two files tying at that "
                f"depth is a naming collision this bridge refuses to break by "
                f"guessing which one coverage.py meant. This is an infra "
                f"failure, never evidence. Next: rename one of the colliding "
                f"test files, or teach `mutation._resolve_module` a "
                f"tie-breaker with a real reason to prefer one over the other."
            )
        if winners:
            return winners[0], k
    return None


def select_tests(
    coverage: CoverageReport, collected: tuple[str, ...], path: str, line: int
) -> tuple[str, ...] | None:
    """Map one line's coverage contexts to the nodeids that covered it.

    A context is an importable dotted name (`test_minirepo.test_parse_range_basic`,
    `test_utils.test_make_default_short_help.test_make_default_short_help`,
    `tests.test_columns.test_render`): coverage.py's `dynamic_context =
    test_function` writes `f"{module}.{qualname}"`, where `module` is the
    dotted name pytest imported the test file under, one segment per
    `__init__.py`'d directory between the file and the first ancestor that is
    not one (`tests/` contributes nothing for a flat test directory, one
    segment for a package, however deep). `collected` carries nodeids
    (`tests/test_utils/test_x.py::test_fn[case]`). `_resolve_module` finds
    which collected file `module` names, by the longest leading run of its
    dotted segments that equals the end of that file's own path segments
    (`_path_segments`); what is left over is matched against the nodeid's
    `::` parts, with any `[param]` suffix stripped from the last one so a
    parametrized family collapses to the whole family (every nodeid sharing
    that stripped qualname), a superset the spec accepts.

    `None` means the line carried no non-empty context: either it is absent
    from `coverage.contexts[path]` entirely, or every context recorded there
    is `""` (import-time execution). Uncovered, never a bridge failure.

    A non-empty context that resolves to no file, or resolves to a file but
    matches no nodeid there, raises `SkepticInfraError`: the naming bridge
    assumed something about how pytest or coverage spells a name that this
    repo does not honor, and running the mutant against nothing would
    silently score it uncovered instead of failing loud. A context that
    resolves to more than one file at its longest matching depth raises from
    `_resolve_module` itself, with its own message.
    """
    contexts = coverage.contexts.get(path, {}).get(line, ())
    non_empty = sorted({c for c in contexts if c})
    if not non_empty:
        return None
    files = {file: _path_segments(file)
            for file in {nodeid.split("::")[0] for nodeid in collected}}
    matched: set[str] = set()
    for context in non_empty:
        context_parts = tuple(context.split("."))
        resolved = _resolve_module(context_parts, files)
        family: list[str] = []
        if resolved is not None:
            winning_file, consumed = resolved
            qual = list(context_parts[consumed:])
            family = [
                nodeid for nodeid in collected
                if (parts := nodeid.split("::"))
                and parts[0] == winning_file
                and _stripped_qualname(parts[1:]) == qual
            ]
        if not family:
            raise SkepticInfraError(
                f"Coverage context {context!r} on {path}:{line} matches no "
                f"nodeid in the collected set. Skeptic resolves a dotted "
                f"coverage context to a `::`-separated nodeid by the longest "
                f"leading run of its dotted segments that equals the end of "
                f"one collected file's own path (so a package prefix, one "
                f"segment per `__init__.py`'d directory, resolves the same "
                f"way a flat test file's bare stem does), then matching what "
                f"is left over against that file's nodeids by qualname. A "
                f"context this bridge cannot place either way would "
                f"otherwise run the mutant against no test at all, reading "
                f"as uncovered rather than as the naming mismatch it is. "
                f"This is an infra failure, never evidence. Next: read the "
                f"collect manifest for {path} and compare its nodeids' file "
                f"path and qualname against {context!r}."
            )
        matched.update(family)
    return tuple(sorted(matched))
