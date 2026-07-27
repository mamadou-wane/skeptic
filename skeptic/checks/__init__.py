"""The check layer: T1 and T2 checks over one `ObservationPair`, and the
frozen evidence schema they all emit into.

`T1_REGISTRY` is what the T1 layer runs, in `CHECK_PRECEDENCE` order. Every
entry is a `(name, callable)` pair, and every callable takes the pair and
nothing else: a check reads no other check's results, so one can be removed
from this tuple without touching the rest. The aggregator that folds check
results into a verdict lands at M4.
"""
from collections.abc import Callable

from skeptic.checks import t1_config, t1_goldens, t1_scope
from skeptic.checks.evidence import CheckResult
from skeptic.checks.observations import ObservationPair

T1_REGISTRY: tuple[tuple[str, Callable[[ObservationPair], CheckResult]], ...] = (
    ("t1_config", t1_config.run),
    ("t1_scope", t1_scope.run),
    ("t1_goldens", t1_goldens.run),
)
