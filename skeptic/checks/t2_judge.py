"""Fold an interpretable diff judgment into evidence.

A missing, invalid, or legacy-unclassified judgment is an incomplete mandatory
check, never a negative finding. Invalid output does not invent hack evidence.
"""
from __future__ import annotations

import time

from skeptic.checks._util import elapsed_ms, write_artifact
from skeptic.checks.evidence import CheckResult, Evidence
from skeptic.checks.observations import ObservationPair
from skeptic.errors import SkepticInfraError

CHECK = "t2_judge"
RULE = "judge_flag"


def run(pair: ObservationPair) -> CheckResult:
    started = time.monotonic()
    report = pair.candidate.judge
    if report is None:
        raise SkepticInfraError(
            "The candidate observation carries no judge report. `t2_judge` "
            "reads `pair.candidate.judge`, which `skeptic verify`'s "
            "enrichment step sets between `collect_pair` and the check "
            "layer; a pair reaching this check with the field unset means "
            "that enrichment never ran, or ran and left the field the way "
            "it found it. This is a harness bug, never evidence: a missing "
            "judge observation is not the same claim as a candidate the "
            "judge read and did not flag. Next: build the pair through "
            "`skeptic verify` (which runs the enrichment), or set "
            "`candidate.judge` explicitly for a unit test."
        )

    if report.parse_status != "valid":
        write_artifact(pair, CHECK, {
            "check": CHECK, "status": "infra", "report": report.model_dump(mode="json"),
        })
        raise SkepticInfraError(
            "The mandatory judge did not produce an interpretable result. "
            "No adverse evidence is inferred from a parse failure. "
            "Next: inspect the recorded judge response; use a fresh workdir for a new judgment."
        )

    artifact = write_artifact(pair, CHECK, {
        "check": CHECK,
        "status": "completed",
        "report": report.model_dump(mode="json"),
    })

    if not report.flagged:
        return CheckResult(check=CHECK, status="completed", evidence=(),
                           artifact=artifact, dur_ms=elapsed_ms(started))

    evidence = (Evidence(
        check=CHECK, rule=RULE, category=report.category, severity="soft",
        detail=f"{report.category}: {report.rationale}",
        artifact=artifact, location=None,
    ),)
    return CheckResult(check=CHECK, status="completed", evidence=evidence,
                       artifact=artifact, dur_ms=elapsed_ms(started))
