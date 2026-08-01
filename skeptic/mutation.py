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

**Sampling and "enclosing function."** The sampling contract's stratum key
is `(path, enclosing function, operator)`, but `Mutant` (the brief's exact
shape) carries no function-identity field, and `sample_mutants` receives
only a `Sequence[Mutant]`, no spans and no tree: it cannot look up which
function a `line` sits inside after the fact. The narrowest reading that is
actually implementable from the given shape: the stratum key uses the
mutant's own `line` in place of "enclosing function". This is finer-grained
than true function-level grouping (a function mutated at two different
lines becomes two strata instead of one), and every pinned property holds
under it regardless: two calls with the same seed still agree exactly, two
different seeds still diverge, the budget is still a hard cap, and changed
population still sorts ahead of caller population in the round-robin
(`population` is folded into the sort key's leading element, `changed`
before `caller`, so a tight budget exhausts on changed strata first).

**Determinism.** `sample_mutants` groups by `(0 if changed else 1, path,
line, operator)`, shuffles each group once with a `random.Random(seed)`
advanced in sorted-key order (so the sequence of shuffle calls never
depends on the input list's own order), then round-robins across the
sorted keys taking one mutant per stratum per pass until `budget` is
reached or every stratum is empty.
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

from skeptic.checks.observations import ObservationPair, parse_unified_diff

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
    population: Literal["changed", "caller"]
    mutated_source: str                  # full post-mutation file text (ast.unparse), or "" when invalid
    valid: bool                          # compile() succeeded at generation time


def _under(path: str, prefixes: list[str]) -> bool:
    """Whether `path` is one of `prefixes` or sits inside one of them.

    A local copy of `skeptic.checks._util.under`, whose docstring declares it
    private to `skeptic.checks`; this module lives outside that package.
    """
    return any(path == p.rstrip("/") or path.startswith(p.rstrip("/") + "/") for p in prefixes)


def _line_overlaps(span: tuple[int, int], ranges: tuple[tuple[int, int], ...]) -> bool:
    start, end = span
    return any(start <= r_end and r_start <= end for r_start, r_end in ranges)


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
        if _under(path, test_dirs) or Path(path).name == CONFTEST:
            continue
        module = ast.parse((pair.candidate.tree / path).read_text())
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
        module = ast.parse((pair.candidate.tree / path).read_text())
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
        if _under(path, test_dirs) or Path(path).name == CONFTEST:
            continue
        module = ast.parse((pair.candidate.tree / path).read_text())
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
    path: str, line: int, operator: str, occurrence: int,
    population: Literal["changed", "caller"], mutated_module: ast.Module,
) -> Mutant:
    mutant_id = _mutant_id(path, line, operator, occurrence)
    try:
        source = ast.unparse(mutated_module)
        compile(source, path, "exec")
    except (SyntaxError, ValueError):
        return Mutant(mutant_id=mutant_id, path=path, line=line, operator=operator,
                      population=population, mutated_source="", valid=False)
    return Mutant(mutant_id=mutant_id, path=path, line=line, operator=operator,
                  population=population, mutated_source=source, valid=True)


def _mutants_for_file(
    path: str, module: ast.Module, spans: tuple[tuple[int, int], ...],
    population: Literal["changed", "caller"],
) -> list[Mutant]:
    nodes = list(ast.walk(module))
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
                occ = _next_occurrence(line, operator)
                mutated = copy.deepcopy(module)
                parent = list(ast.walk(mutated))[parent_idx]
                del getattr(parent, attr)[pos]
                results.append(_finish(path, line, operator, occ, population, mutated))
            continue
        matcher, applier = _OPERATOR_TABLE[operator]
        for node_idx, node in enumerate(nodes):
            lineno = getattr(node, "lineno", None)
            if lineno is None or not _line_overlaps((lineno, lineno), spans):
                continue
            for slot in matcher(node):
                occ = _next_occurrence(lineno, operator)
                mutated = copy.deepcopy(module)
                target = list(ast.walk(mutated))[node_idx]
                applier(target, slot)
                results.append(_finish(path, lineno, operator, occ, population, mutated))
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
        for path in sorted(spans_by_path):
            module = ast.parse((pair.candidate.tree / path).read_text())
            results.extend(_mutants_for_file(path, module, spans_by_path[path], population))
    return tuple(results)


def sample_mutants(mutants: Sequence[Mutant], budget: int, seed: int) -> tuple[Mutant, ...]:
    """Stratified, seeded, budget-capped sample. See the module docstring's
    "Sampling and enclosing function" and "Determinism" sections for the
    stratum key and the round-robin's exact order."""
    grouped: dict[tuple[int, str, int, str], list[Mutant]] = {}
    for mutant in mutants:
        rank = 0 if mutant.population == "changed" else 1
        key = (rank, mutant.path, mutant.line, mutant.operator)
        grouped.setdefault(key, []).append(mutant)

    rng = random.Random(seed)
    ordered_keys = sorted(grouped)
    pools: dict[tuple[int, str, int, str], list[Mutant]] = {}
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
