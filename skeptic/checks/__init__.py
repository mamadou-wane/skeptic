"""The check layer: T1 and T2 checks over one `ObservationPair`, and the
frozen evidence schema they all emit into.

`T1_REGISTRY` is what the T1 layer runs, in `CHECK_PRECEDENCE` order. Every
entry is a `(name, callable)` pair, and every callable takes the pair and
nothing else: a check reads no other check's results, so one can be removed
from this tuple without touching the rest. The aggregator that folds check
results into a verdict lands at M4.

`t1_ast` is deliberately outside the registry. The registry is the set of
checks with a verdict-list status and `t1_ast` has none: it carries the
`"attribution"` status, appears in neither `checks_completed` nor
`not_applicable`, and rewrites the other checks' evidence after they have run.
`run_t1_layer` composes the two halves, so M4's aggregator and Task 14's matrix
test call one function rather than reimplementing the sequence.
"""
from collections.abc import Callable

from skeptic.checks import t1_ast, t1_config, t1_goldens, t1_scope
from skeptic.checks.evidence import CheckResult
from skeptic.checks.observations import ObservationPair

T1_REGISTRY: tuple[tuple[str, Callable[[ObservationPair], CheckResult]], ...] = (
    ("t1_config", t1_config.run),
    ("t1_scope", t1_scope.run),
    ("t1_goldens", t1_goldens.run),
)


def run_t1_layer(pair: ObservationPair) -> tuple[CheckResult, ...]:
    """Every registered check, then `t1_ast`'s row, then its annotations.

    The attribution result is last, which is where `CHECK_PRECEDENCE` puts
    `t1_ast` among the T1 checks. `annotate` preserves the order it is given.
    """
    results = tuple(run(pair) for _, run in T1_REGISTRY) + (t1_ast.run(pair),)
    return t1_ast.annotate(pair, results)
