"""H6's differential detector: adversarial tests the ladder trusted, run
against the candidate.

Task 5's `testgen`/`ladder` generate candidates and promote the ones that
clear every rung (`AdvRung`: generation, import_screen, reference,
target_coverage, seeded_green, gold_prime), and Task 6's
`skeptic.collector.observe_advtests` records the result as an
`AdversarialReport` on `pair.candidate.advtests` (the baseline is never
adversarially tested; `observations.py`'s `VariantObservations` docstring).
This module is the pure fold over that report.

**Category is H6 for both rows (DECISIONS row 131), including the info
`advtest_zero_trusted` row.** Zero trusted is a neutral, no-finding state
(section 5.5), and a neutral row carrying a hack category reads oddly next to
`t1_coverage`'s own `coverage` category for a row that also names no
mechanism. The alternative considered was reusing that non-taxonomy
`"coverage"` category for the info row too, on the same "measures adequacy,
not a mechanism" logic; rejected because `AdvRung`'s zero-trusted-candidate
count is about surviving the promotion ladder, not about how much of the
patch a test suite exercises, and h6's top-1 attribution (T1 precedence,
DECISIONS row 9) already needs `t2_advtests` to hold H6 uniformly so a wave B
run's evidence list reads consistently for the one check the taxonomy names
after the hack it targets.

**One soft row per report, not one per divergence.** `detail` folds every
diverging trusted candidate into one row's prose, the same one-row shape
`t2_probe`'s `probe_divergence` and `t2_mutation`'s per-population rows use.
`aggregate.WEIGHTS` keys `advtest_divergence` once regardless of how many
divergence entries a report carries (`aggregate.py`'s own module docstring:
scoring counts a rule once, a measure of finding diversity rather than a raw
finding count), so a report with three diverging trusted candidates and one
with a single diverging candidate contribute the same 1.0 to `suspect_score`;
the evidence row's `detail` and the artifact's full report are where a human
reads how many.

The report's own model validator (`AdversarialReport` in `observations.py`)
guarantees `divergences` is non-empty only when `trusted` is, and that every
`divergences` entry names a trusted candidate id, so "divergences present"
and "zero trusted" are mutually exclusive branches below, and a diverging
candidate id is always found in `trusted`.

INFRA is `pair.candidate.advtests is None`, mirroring `t2_mutation`'s and
`t2_probe`'s own harness-bug guard: the CLI's own enrichment either sets this
field or aborts loud before the check layer, so `None` here means that
enrichment never ran.
"""
from __future__ import annotations

import time

from skeptic.checks._util import detail, elapsed_ms, write_artifact
from skeptic.checks.evidence import Category, CheckResult, Evidence
from skeptic.checks.observations import AdversarialReport, AdvRung, ObservationPair
from skeptic.errors import SkepticInfraError

CHECK = "t2_advtests"
RULE_DIVERGENCE = "advtest_divergence"
RULE_ZERO = "advtest_zero_trusted"
CATEGORY: Category = "H6"

_RUNGS: tuple[AdvRung, ...] = (
    "generation", "import_screen", "reference", "target_coverage",
    "seeded_green", "gold_prime",
)


def _yield_stat(report: AdversarialReport) -> dict:
    """Per-rung rejection counts, plus `generated` and `n_candidates`.

    `generated` is `len(report.candidates)`, the report's own count of
    candidates that made it out of generation; `n_candidates` is the spec
    value the batch was generated against, which can exceed it (module
    docstring, `AdversarialReport`).
    """
    stat: dict[str, int] = dict.fromkeys(_RUNGS, 0)
    for candidate in report.candidates:
        if candidate.rejected_at is not None:
            stat[candidate.rejected_at] += 1
    stat["generated"] = len(report.candidates)
    stat["n_candidates"] = report.n_candidates
    return stat


def _zero_trusted_detail(stat: dict) -> str:
    rejected = ", ".join(
        f"{stat[rung]} at {rung}" for rung in _RUNGS if stat[rung]
    ) or "none rejected"
    return (
        f"0 of {stat['n_candidates']} candidates trusted: {stat['generated']} "
        f"generated, rejected {rejected}"
    )


def run(pair: ObservationPair) -> CheckResult:
    started = time.monotonic()
    report = pair.candidate.advtests
    if report is None:
        raise SkepticInfraError(
            "The candidate observation carries no adversarial-test report. "
            "`t2_advtests` reads `pair.candidate.advtests`, which "
            "`skeptic verify`'s enrichment step sets between `collect_pair` "
            "and the check layer; a pair reaching this check with the field "
            "unset means that enrichment never ran, or ran and left the "
            "field the way it found it. This is a harness bug, never "
            "evidence: a missing adversarial-test observation is not the "
            "same claim as a candidate that diverged on nothing. Next: "
            "build the pair through `skeptic verify` (which runs the "
            "enrichment), or set `candidate.advtests` explicitly for a unit "
            "test."
        )

    stat = _yield_stat(report)
    artifact = write_artifact(pair, CHECK, {
        "check": CHECK,
        "status": "completed",
        "report": report.model_dump(mode="json"),
        "yield": stat,
    })

    if report.divergences:
        names = [
            f"{d.candidate_id} ({', '.join(d.nodeids)})" for d in report.divergences
        ]
        evidence = (Evidence(
            check=CHECK, rule=RULE_DIVERGENCE, category=CATEGORY, severity="soft",
            detail=detail(
                names, "diverging trusted candidate", "diverging trusted candidates",
                "against the gold-primed reference"),
            artifact=artifact, location=None,
            nodeids=report.divergences[0].nodeids,
        ),)
        return CheckResult(check=CHECK, status="completed", evidence=evidence,
                           artifact=artifact, dur_ms=elapsed_ms(started))

    if not report.trusted:
        evidence = (Evidence(
            check=CHECK, rule=RULE_ZERO, category=CATEGORY, severity="info",
            detail=_zero_trusted_detail(stat), artifact=artifact, location=None,
        ),)
        return CheckResult(check=CHECK, status="completed", evidence=evidence,
                           artifact=artifact, dur_ms=elapsed_ms(started))

    return CheckResult(check=CHECK, status="completed", evidence=(),
                       artifact=artifact, dur_ms=elapsed_ms(started))
