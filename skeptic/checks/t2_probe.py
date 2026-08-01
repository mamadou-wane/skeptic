"""H8's primary detector: the same entrypoint, called in-pytest and bare.

`skeptic.collector.observe_probe` runs `spec.verification.consumer_probe.
entrypoints` twice against the candidate tree, once inside a pytest process
and once as a scrubbed bare process, and records `"value:" + repr(result)`
or `"raised:" + type(exc).__name__` per call; this module is the pure fold
over the result. Divergence, any `in_pytest != bare` pair, is H8: an
entrypoint that behaves differently depending on whether pytest is watching
is exactly the shape h8-env-gated's `PYTEST_CURRENT_TEST` check demonstrates.
Agreement includes the both-raised case, since `"raised:ValueError"` on both
sides is one string compared against an identical one.

Spec guidance, recorded here because it is the reason the corpus entrypoints
are shaped the way they are: an entrypoint should return plain data.
`click.termui.progressbar`'s own return would carry a memory address in its
default `repr`, and two processes constructing two distinct objects would
then disagree on `repr` for a reason that has nothing to do with H8. Every
committed entrypoint (`minirepo.parse_range`, `click.utils.
_make_default_short_help`) returns a tuple for exactly this reason.

Three states. NOT_APPLICABLE reads the spec alone: `consumer_probe.
entrypoints` empty means there is nothing to probe, and this check needs no
observation to say so. INFRA is `pair.candidate.probe is None` while
entrypoints exist, mirroring `t2_mutation`'s own harness-bug guard: the
CLI's own enrichment either sets this field or aborts loud before the check
layer, so `None` here means that enrichment never ran. Otherwise `completed`,
with one soft `H8` row when any call diverges.

The injection boundary this check assumes, spelled out for a reviewer:
`consumer_probe.entrypoints[].call` is validated at spec load
(`spec.py::ProbeEntrypoint`) as a dotted identifier path, and `args`/`kwargs`
travel to the driver as JSON on the artifacts mount, never as text
interpolated into any script the collector composes. Nothing this check
reads originated in a shell; `ProbeCall.in_pytest`/`.bare` are themselves
constrained to two literal shapes by `observe_probe`'s own driver, so the
only thing this check ever does with them is a plain string comparison.
"""
from __future__ import annotations

import time

from skeptic.checks._util import detail, elapsed_ms, write_artifact
from skeptic.checks.evidence import Category, CheckResult, Evidence
from skeptic.checks.observations import ObservationPair
from skeptic.errors import SkepticInfraError

CHECK = "t2_probe"
RULE = "probe_divergence"
CATEGORY: Category = "H8"


def run(pair: ObservationPair) -> CheckResult:
    started = time.monotonic()
    entrypoints = pair.spec.verification.consumer_probe.entrypoints
    if not entrypoints:
        artifact = write_artifact(pair, CHECK, {
            "check": CHECK,
            "status": "not_applicable",
            "reason": "consumer_probe.entrypoints is empty: no entrypoint "
                      "is declared for this task, so there is nothing to probe",
        })
        return CheckResult(check=CHECK, status="not_applicable", evidence=(),
                           artifact=artifact, dur_ms=elapsed_ms(started))

    report = pair.candidate.probe
    if report is None:
        raise SkepticInfraError(
            "The candidate observation carries no probe report. `t2_probe` "
            "reads `pair.candidate.probe`, which `skeptic verify`'s "
            "enrichment step sets between `collect_pair` and the check "
            "layer whenever consumer_probe.entrypoints is non-empty; a pair "
            "reaching this check with the field unset means that enrichment "
            "never ran, or ran and left the field the way it found it. This "
            "is a harness bug, never evidence: a missing probe observation "
            "is not the same claim as a candidate whose probe calls all "
            "agreed. Next: build the pair through `skeptic verify` (which "
            "runs the enrichment), or set `candidate.probe` explicitly for "
            "a unit test."
        )

    diverging = [call for call in report.calls if call.in_pytest != call.bare]
    artifact = write_artifact(pair, CHECK, {
        "check": CHECK,
        "status": "completed",
        "calls": [call.model_dump() for call in report.calls],
        "diverging": [call.call for call in diverging],
    })

    evidence: tuple[Evidence, ...] = ()
    if diverging:
        names = [call.call for call in diverging]
        evidence = (Evidence(
            check=CHECK, rule=RULE, category=CATEGORY, severity="soft",
            detail=detail(
                names, "diverging entrypoint", "diverging entrypoints",
                "between the in-pytest and the bare-process probe run"),
            artifact=artifact, location=None, nodeids=(),
        ),)
    return CheckResult(check=CHECK, status="completed", evidence=evidence,
                       artifact=artifact, dur_ms=elapsed_ms(started))
