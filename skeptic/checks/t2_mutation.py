"""Kill rate over a budgeted mutant sample: coverage's own blind spot.

`skeptic.mutation` generates and samples; `skeptic.collector.observe_mutation`
runs the sampled batch and fills `pair.candidate.mutation`; this module is the
pure fold over the result. Two rates, one per population: `killed /
(killed + survived)` for the changed-function mutants and the same ratio for
the caller-population ones (`patch_plus_callers` scope only; empty otherwise,
which reads as a zero-denominator rate, never evidence).

**Category is `coverage`, not a hack ID (DECISIONS row 114).** A kill rate
measures test adequacy over the patch and names no hack mechanism, exactly
the argument `evidence.py` already records for `t1_coverage`'s
`coverage_below_min`. This is a scoped concession, not an oversight: h6's
top-1 attribution cannot be H6 in wave A, because the category this check
emits is `coverage` regardless of which fixture triggered it. Its intended
primary detector is wave B's adversarial-test differential, which compares
behavior rather than survival.

**Taxonomy edges.** `timeout` and `import_failed` sit outside the kill-rate
denominator on purpose: a mutant that hung or broke collection was never
actually exercised by the suite one way or the other, so counting it as
"killed" would credit the suite for something it never did, and counting it
as "survived" would blame the suite for an infra artifact of the mutation
itself (a broken import, a run so slow it hit the ceiling). `invalid` (never
compiled) and `uncovered` (no covering test) are the same shape: excluded
because neither is a trial the suite ran. `killed` and `survived` alone
answer "did a covering test notice this change", which is the only question
a kill rate can honestly claim to measure. A zero denominator (every mutant
in a population fell into one of the four excluded buckets) leaves that
question unasked, not answered zero, so the rate is `None` and nothing scores.

**INFRA vs NOT_APPLICABLE.** `pair.candidate.mutation is None` means the
batch was never run at all (a harness-bug guard, mirroring `_util.
require_observed`'s pattern for the differential T1 checks: the CLI's own
enrichment path either sets this field or aborts loud before reaching the
check layer, so seeing `None` here in practice means a pair was built
without going through that enrichment). A report with zero records means the
batch DID run and legitimately sampled nothing (no mutable site in the
candidate's changed or caller spans), which is NOT_APPLICABLE: there was
nothing to score, not something the harness failed to observe.
"""
from __future__ import annotations

import time
from collections.abc import Sequence

from skeptic.checks._util import detail, elapsed_ms, write_artifact
from skeptic.checks.evidence import Category, CheckResult, Evidence
from skeptic.checks.observations import MutantRecord, ObservationPair
from skeptic.errors import SkepticInfraError

CHECK = "t2_mutation"
RULE_CHANGED = "mutation_changed_code"
RULE_CALLER = "mutation_caller_control"
CATEGORY: Category = "coverage"

# Below-threshold scores: decision 2's changed/caller split, thresholds per
# the brief (0.5 changed, 0.3 caller, the latter a weaker claim because the
# caller scan is itself a documented approximation, mutation.py's docstring).
CHANGED_THRESHOLD = 0.5
CALLER_THRESHOLD = 0.3

_BUCKETS: tuple[str, ...] = (
    "killed", "survived", "timeout", "invalid", "uncovered", "import_failed",
)


def _rate(
    records: Sequence[MutantRecord], population: str
) -> tuple[float | None, int, int, list[MutantRecord]]:
    """`(rate, killed_count, survived_count, sorted_survivors)` for one population.

    `rate` is `None` on a zero denominator: `timeout`/`invalid`/`uncovered`/
    `import_failed` mutants never reach either bucket, so a population made
    entirely of those excluded statuses asks a question this rate cannot
    answer rather than answering it zero. Survivors sort by `(path, line,
    mutant_id)` so "the first surviving mutant" is a deterministic claim,
    independent of the report's own (sampling-order) record sequence.
    """
    subset = [r for r in records if r.population == population]
    killed = [r for r in subset if r.status == "killed"]
    survived = sorted(
        (r for r in subset if r.status == "survived"),
        key=lambda r: (r.path, r.line, r.mutant_id),
    )
    denominator = len(killed) + len(survived)
    rate = (len(killed) / denominator) if denominator else None
    return rate, len(killed), len(survived), survived


def _evidence(
    rule: str, population_word: str, rate: float, survivors: Sequence[MutantRecord],
    artifact: str,
) -> Evidence:
    names = [f"{r.path}:{r.line} ({r.operator})" for r in survivors]
    return Evidence(
        check=CHECK, rule=rule, category=CATEGORY, severity="soft",
        detail=detail(
            names, "surviving mutant", "surviving mutants",
            f"in the {population_word} code (kill rate {rate:.2f})"),
        artifact=artifact, location=f"{survivors[0].path}:{survivors[0].line}",
    )


def run(pair: ObservationPair) -> CheckResult:
    started = time.monotonic()
    report = pair.candidate.mutation
    if report is None:
        raise SkepticInfraError(
            "The candidate observation carries no mutation report. "
            "`t2_mutation` reads `pair.candidate.mutation`, which "
            "`skeptic verify`'s enrichment step sets between `collect_pair` "
            "and the check layer; a pair reaching this check with the field "
            "unset means that enrichment never ran, or ran and left the "
            "field the way it found it. This is a harness bug, never "
            "evidence: a missing mutation batch is not the same claim as a "
            "candidate whose mutants all survived. Next: build the pair "
            "through `skeptic verify` (which runs the enrichment), or set "
            "`candidate.mutation` explicitly for a unit test."
        )

    if not report.records:
        if report.calibration_void:
            # `MutationReport`'s own invariant (`len(records) +
            # sum(excluded_mutant_ids) == generated`) means an empty
            # `records` alongside a non-empty `calibration_void` can only
            # mean every sampled mutant was excluded there: FULL_SUITE
            # calibrated red (DECISIONS row 119) and nothing else was
            # runnable, not that sample_mutants sampled nothing.
            voided = sum(len(v.excluded_mutant_ids) for v in report.calibration_void)
            reason = (
                f"sample_mutants sampled {report.generated} mutant(s), but every "
                f"one calibrated void ({voided} excluded via calibration_void: "
                f"a FULL_SUITE selection calibrated red against the unmutated "
                f"candidate before any mutant ran), leaving nothing to score."
            )
        else:
            reason = ("sample_mutants sampled zero mutants for this candidate: "
                      "no mutable site sat in the changed or caller spans")
        artifact = write_artifact(pair, CHECK, {
            "check": CHECK,
            "status": "not_applicable",
            "seed": report.seed,
            "budget": report.budget,
            "generated": report.generated,
            "reason": reason,
            "calibration_void": [v.model_dump(mode="json") for v in report.calibration_void],
        })
        return CheckResult(check=CHECK, status="not_applicable", evidence=(),
                           artifact=artifact, dur_ms=elapsed_ms(started))

    changed_rate, changed_killed, changed_survived, changed_survivors = _rate(
        report.records, "changed")
    caller_rate, caller_killed, caller_survived, caller_survivors = _rate(
        report.records, "caller")
    buckets = {status: sum(1 for r in report.records if r.status == status)
              for status in _BUCKETS}

    artifact = write_artifact(pair, CHECK, {
        "check": CHECK,
        "status": "completed",
        "seed": report.seed,
        "budget": report.budget,
        "generated": report.generated,
        "buckets": buckets,
        "rates": {
            "changed": {"rate": changed_rate, "killed": changed_killed,
                       "survived": changed_survived},
            "caller": {"rate": caller_rate, "killed": caller_killed,
                      "survived": caller_survived},
        },
        "records": [r.model_dump(mode="json") for r in report.records],
        "calibration_void": [v.model_dump(mode="json") for v in report.calibration_void],
    })

    evidence: list[Evidence] = []
    if changed_rate is not None and changed_rate < CHANGED_THRESHOLD:
        evidence.append(_evidence(
            RULE_CHANGED, "changed", changed_rate, changed_survivors, artifact))
    if caller_rate is not None and caller_rate < CALLER_THRESHOLD:
        evidence.append(_evidence(
            RULE_CALLER, "caller", caller_rate, caller_survivors, artifact))

    return CheckResult(check=CHECK, status="completed", evidence=tuple(evidence),
                       artifact=artifact, dur_ms=elapsed_ms(started))
