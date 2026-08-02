"""The catch-all: one LLM read of the candidate diff against the hack-smell
rubric, folded into evidence.

`skeptic.judge.judge_diff` makes one Skeptic-model call over the candidate's
diff text and parses the answer onto a `JudgeReport`
(`skeptic.checks.observations`); this module is the pure fold over that
report, the same enrichment-plus-pure-fold shape `t2_advtests` and `t2_probe`
use. `parse_judge_response`'s own fail-closed contract (plan decision 8,
`skeptic/judge.py`) means a `JudgeReport` never reaches this check flagged
with `category=None`: an unparseable response or an out-of-taxonomy category
both come back `flagged=False`, so the branch below that reads
`report.category` for a flagged report always has a valid `Category` to hand
`Evidence`.

INFRA is `pair.candidate.judge is None`, mirroring `t2_mutation`'s,
`t2_probe`'s, and `t2_advtests`'s own harness-bug guard: the CLI's own
enrichment either sets this field or aborts loud before the check layer, so
`None` here means that enrichment never ran.
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
