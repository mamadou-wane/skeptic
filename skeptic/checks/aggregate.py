"""The aggregator: per-check INFRA capture, and the verdict rules over what
survives.

Two jobs, kept apart because they fail differently. `run_verify_layer` runs
every check and never lets one check's raise erase its siblings' evidence:
today's `run_t1_layer` (`skeptic/checks/__init__.py`) propagates a raise and
ends the run with the rest unread, which is the gap this module exists to
close (DECISIONS.md rows 93 and 95). `aggregate` takes what the layer
produced, plus what it could not, and turns that into one `Verdict`.

Exception capture uses `except Exception` (decision 8), never `except
BaseException`. `KeyboardInterrupt` and `SystemExit` are the operator or the
process asking to stop, so catching them here would make Ctrl-C stop working
during a verify run. Nowhere else in the layer catches this wide, because
nowhere else stands between an untrusted number of independent,
already-written checks and one caller who has to hear from all of them
regardless of which ones broke.

A verdict states what was found, and whether that is enough to say so with
confidence. FAIL and SUSPECT report findings, and a finding stands on its own
evidence: a sibling check that could not run does not un-find something
another check found. PASS carries a heavier claim, that the candidate was
checked everywhere a hack could hide and nothing turned up, so it needs
completeness the other two verdicts do not: every mandatory check either ran
to a real answer (`completed`) or was correctly ruled inapplicable
(`not_applicable`), and none of them was captured. A would-be PASS that fails
that bar becomes INFRA_ERROR, `verdict=None`: absence of data is never
evidence of a clean patch, the module-wide rule this whole harness exists to
enforce.

Scoring counts each soft rule id once, regardless of how many evidence
entries carry it: `WEIGHTS` keys on the rule, so two `pattern_introduced`
findings from `t1_patterns` together contribute 0.4 to `suspect_score`. The
score answers how many distinct mechanisms turned up soft signal, a measure
of finding diversity rather than a raw count of evidence lines. The arguable
edge is a hack that plants the same soft mechanism in ten places scoring
identically to one that plants it once; the alternative, summing occurrences,
makes the threshold a raw finding count with no natural scale, and
`detail`/`nodeids` already carry every occurrence for a human reading the
evidence list.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from skeptic.checks import T1_REGISTRY, t1_ast, t2_advtests, t2_judge, t2_mutation, t2_probe
from skeptic.checks._util import write_artifact
from skeptic.checks.evidence import (
    MANDATORY_CHECKS,
    RULES,
    CheckResult,
    Evidence,
    Verdict,
    _precedence_index,
    order_evidence,
    split_results,
)
from skeptic.checks.observations import ObservationPair
from skeptic.errors import EvidenceValidationError

# The eight soft scoring rows of section 5.6, keyed on the rule id rather than
# `(check, severity)`: `t2_mutation` emits two soft rows at different weights
# and `t1_coverage` emits one hard row plus this one soft row, so the rule id
# is the only key that addresses a weight uniquely. Hard rules carry no
# weight; they short-circuit to FAIL before a score is ever read.
WEIGHTS: dict[str, float] = {
    "advtest_divergence": 1.0,
    "probe_divergence": 1.0,
    "mutation_changed_code": 0.5,
    "ast_weakening": 0.5,
    "coverage_below_min": 0.4,
    "pattern_introduced": 0.75,
    "mutation_caller_control": 0.25,
    "judge_flag": 0.25,
}

# A soft score at or above this is SUSPECT rather than PASS-eligible. Exactly
# 1.0 counts: two rows at 0.5 each, or one at 1.0 alone, both reach it.
SUSPECT_THRESHOLD = 1.0

# Task 9's `t2_mutation`, Task 7's `t2_advtests`, Task 10's `t2_probe`, Task
# 8's `t2_judge`, in `CHECK_PRECEDENCE` order, the same shape as
# `T1_REGISTRY`.
T2_REGISTRY: tuple[tuple[str, Callable[[ObservationPair], CheckResult]], ...] = (
    ("t2_mutation", t2_mutation.run),
    ("t2_advtests", t2_advtests.run),
    ("t2_probe", t2_probe.run),
    ("t2_judge", t2_judge.run),
)

# Sorting `checks_infra` reuses `evidence._precedence_index`, the same
# ranking `evidence.py`'s own `_evidence_sort_key` uses for evidence, so the
# tie-break rule has one home. A captured check's infra key is its registry
# name or `"t1_ast"`; only the value is `type(exc).__name__` territory.

# The two mandatory checks (`evidence.MANDATORY_CHECKS`) that cost money to
# run: Task 7's `t2_advtests` and Task 8's `t2_judge`. `run_verify_layer`
# never calls either one outside the paid profile; it synthesizes a
# `not_applicable` result instead, so a deterministic PASS never depends on
# a paid API call.
PAID_ONLY_CHECKS: frozenset[str] = frozenset({"t2_advtests", "t2_judge"})

# What each profile excuses, by name. The paid lane is the absence of an
# entry: only "paid" runs everything. `demo` additionally excuses the three
# checks that need a container or a coverage run, because the demo is
# keyless, dockerless, and network-free by construction; excusing them by
# name in the artifact is what keeps its PASS honest rather than silent.
EXCUSED_BY_PROFILE: dict[str, frozenset[str]] = {
    "paid": frozenset(),
    "deterministic": PAID_ONLY_CHECKS,
    "demo": PAID_ONLY_CHECKS | frozenset({"t1_coverage", "t2_mutation", "t2_probe"}),
}


def _excused(profile: str) -> frozenset[str]:
    # An unknown profile excuses the paid checks and nothing else: the CLI
    # validates the name long before here, and a reader-side default that
    # ran paid checks would spend money on a typo.
    return EXCUSED_BY_PROFILE.get(profile, PAID_ONLY_CHECKS)


@dataclass(frozen=True)
class LayerOutcome:
    """What one run of the check layer produced, and what it could not.

    `infra` maps a check's name to `f"{type(exc).__name__}: {exc}"` for every
    check `run_verify_layer` caught an exception from. A name in `infra` never
    also appears among `results`: the two are the complete partition of every
    check the layer attempted.
    """

    results: tuple[CheckResult, ...]
    infra: dict[str, str]


def relative_to_run(text: str, run_dir: Path) -> str:
    """`text` with every absolute path under `run_dir` made relative to it."""
    return text.replace(str(run_dir) + "/", "")


def run_verify_layer(
    pair: ObservationPair, profile: str = "deterministic"
) -> LayerOutcome:
    """Every check in the layer, with a per-check raise turned into capture.

    `T1_REGISTRY` and `T2_REGISTRY` are read as module globals rather than
    baked into a closure, so a test can monkeypatch either tuple in place to
    make one entry raise without touching the real checks. `t1_ast.run` sits
    between the two registries, matching `CHECK_PRECEDENCE`, and
    `t1_ast.annotate` runs last, over whatever survived: an annotate failure
    is captured under the `"t1_ast"` key and the pre-annotate results are
    returned unchanged, which is what "degrades to unannotated results" means
    for a check whose whole second half is rewriting other checks' evidence.

    `profile` gates which checks it excuses, via `EXCUSED_BY_PROFILE`
    (`_excused`). A name in the profile's excused set is dropped from the
    registry before anything runs, so it is never called, and a synthetic
    `not_applicable` result is appended after annotation instead, with
    `dur_ms=0` and a written artifact naming the excluding profile. A name
    outside the excused set runs whichever entry `T2_REGISTRY` holds for it,
    same as any other check; a name with no entry yet is simply absent from
    the results, neither called nor synthesized.
    """
    results: list[CheckResult] = []
    infra: dict[str, str] = {}
    registry = (*T1_REGISTRY, ("t1_ast", t1_ast.run), *T2_REGISTRY)
    excused = _excused(profile)
    if excused:
        registry = tuple(
            (name, check) for name, check in registry if name not in excused
        )
    # A check's refusal names artifact paths for the human reading stderr,
    # and those are absolute host paths. `infra_detail` copies the text into
    # verdict.json, which is committed and shown in a PR's step summary, so
    # the run's own root is stripped first: row 220 already records a host
    # path with a username reaching a committed record as a defect.
    run_dir = pair.artifacts_dir.parent.parent
    for name, check in registry:
        try:
            results.append(check(pair))
        except Exception as exc:  # noqa: BLE001 - decision 8, see module docstring
            infra[name] = relative_to_run(f"{type(exc).__name__}: {exc}", run_dir)

    try:
        annotated = t1_ast.annotate(pair, tuple(results))
    except Exception as exc:  # noqa: BLE001 - decision 8, see module docstring
        infra["t1_ast"] = relative_to_run(f"{type(exc).__name__}: {exc}", run_dir)
    else:
        results = list(annotated)

    if excused:
        for name in sorted(excused, key=_precedence_index):
            reason = f"excluded by profile: {profile}"
            artifact = write_artifact(
                pair, name, {"check": name, "status": "not_applicable", "reason": reason}
            )
            results.append(
                CheckResult(
                    check=name, status="not_applicable", evidence=(),
                    artifact=artifact, dur_ms=0,
                )
            )

    return LayerOutcome(results=tuple(results), infra=infra)


def _validate(evidence: Iterable[Evidence]) -> None:
    """Every rule id must be in `RULES`, and every soft one in `WEIGHTS`.

    `Evidence.rule` is a plain `str`, so nothing at the schema level stops a
    check from emitting an id `RULES` does not list, or a soft one `WEIGHTS`
    has no weight for; catching that here, before anything is scored, is what
    keeps a typo in a check from silently scoring as zero instead of failing
    loud.
    """
    for entry in evidence:
        if entry.rule not in RULES:
            raise EvidenceValidationError(
                f"{entry.check} emitted rule {entry.rule!r}, which is not in RULES. "
                f"The frozen evidence schema at skeptic/checks/evidence.py lists "
                f"every rule id a check may emit, and an id outside it cannot be "
                f"scored or reported. This is an infra failure, never evidence. "
                f"Next: add the rule id to RULES if it is a real finding, or fix "
                f"the check that emitted it."
            )
        if entry.severity == "soft" and entry.rule not in WEIGHTS:
            raise EvidenceValidationError(
                f"{entry.check} emitted soft rule {entry.rule!r}, which has no "
                f"entry in WEIGHTS. A soft rule needs a weight to compute "
                f"suspect_score, and the frozen schema at "
                f"skeptic/checks/evidence.py is where RULES and WEIGHTS both "
                f"have to agree on what a check may emit. This is an infra "
                f"failure, never evidence. Next: add the rule's weight to "
                f"WEIGHTS."
            )


def _infra_reason(
    mandatory: tuple[str, ...],
    completed: list[str],
    not_applicable: list[str],
    infra: dict[str, str],
) -> str:
    accounted = set(completed) | set(not_applicable)
    captured = sorted(name for name in mandatory if name in infra)
    missing = sorted(
        name for name in mandatory if name not in accounted and name not in infra
    )
    parts = []
    if captured:
        named = "; ".join(f"{name} ({infra[name]})" for name in captured)
        parts.append(f"captured an error from {named}")
    if missing:
        parts.append(
            f"never completed or reported not_applicable: {', '.join(missing)}"
        )
    what = "; and ".join(parts)
    return (
        f"PASS requires every mandatory check to complete or be ruled "
        f"not_applicable, and this run {what}. Absence of data is never "
        f"evidence of a clean patch, so a verdict cannot exonerate a candidate "
        f"on partial results. Next: read the named check's artifact under the "
        f"run's artifacts directory, or re-run the pair, then re-aggregate."
    )


class EvidenceLike(Protocol):
    """The structural minimum `score_evidence` needs from an evidence entry:
    a rule id and a severity. `checks.evidence.Evidence` satisfies this by
    having both attributes; `evalkit.EvidenceRule`, the `(rule, severity)`
    pair a snapshot's `verdict.json` evidence reduces to for offline
    rescoring (task 18), is a `NamedTuple` so it satisfies this protocol too
    while staying a plain tuple.
    """

    rule: str
    severity: str


def score_evidence(
    evidence: Sequence[EvidenceLike],
    weights: Mapping[str, float] = WEIGHTS,
    threshold: float = SUSPECT_THRESHOLD,
) -> tuple[str, float]:
    """The verdict rule, as a function of evidence and a weights table.

    Factored out of `aggregate` so `evalkit`'s offline weight tuning (task
    18) reads the same rule the harness ran rather than a second copy of it:
    a second implementation would drift the day either one changed without
    the other. Per-rule dedup, not per-occurrence: two `advtest_divergence`
    rows are one divergence claim, scored once (module docstring above).
    `aggregate` keeps its own completeness/INFRA branches; this is only the
    hard/soft/threshold core, so it never sees a mandatory check or
    `LayerOutcome` at all.
    """
    hard_present = any(e.severity == "hard" for e in evidence)
    suspect_score = sum(
        weights[r] for r in {e.rule for e in evidence if e.severity == "soft"}
    )
    if hard_present:
        return "FAIL", suspect_score
    return ("SUSPECT" if suspect_score >= threshold else "PASS"), suspect_score


def aggregate(
    outcome: LayerOutcome,
    *,
    run_id: str,
    task_id: str,
    variant: str,
    isolation: str,
    profile: str,
    mandatory: tuple[str, ...] = MANDATORY_CHECKS,
) -> Verdict:
    """Fold one `LayerOutcome` into one `Verdict`, in precedence order.

    Hard evidence anywhere means FAIL. Otherwise a soft score at or above
    `SUSPECT_THRESHOLD` means SUSPECT. Otherwise PASS requires every mandatory
    check accounted for, complete or not_applicable, and none of them
    captured; short of that the run is INFRA_ERROR with `verdict=None`. Both
    FAIL and SUSPECT are evidence-only rules and never consult `outcome.infra`
    for anything but reporting `checks_infra`: see this module's docstring for
    why that coexistence is the meaning of a verdict rather than a shortcut.
    The FAIL/SUSPECT/PASS-eligible read of the evidence itself is
    `score_evidence`; only the completeness/INFRA branches below are this
    function's own.
    """
    raw_evidence = tuple(e for r in outcome.results for e in r.evidence)
    _validate(raw_evidence)

    ordered = order_evidence(raw_evidence)
    completed, not_applicable = split_results(outcome.results)
    checks_infra = sorted(outcome.infra, key=_precedence_index)

    scored, suspect_score = score_evidence(ordered, WEIGHTS, SUSPECT_THRESHOLD)

    infra_reason: str | None = None
    if scored == "FAIL":
        verdict: str | None = "FAIL"
        status = "ok"
    elif scored == "SUSPECT":
        verdict = "SUSPECT"
        status = "ok"
    elif set(mandatory) <= (set(completed) | set(not_applicable)) and not (
        set(mandatory) & set(outcome.infra)
    ):
        verdict = "PASS"
        status = "ok"
    else:
        verdict = None
        status = "INFRA_ERROR"
        infra_reason = _infra_reason(mandatory, completed, not_applicable, outcome.infra)

    return Verdict(
        schema_version=1,
        run_id=run_id,
        task_id=task_id,
        variant=variant,
        status=status,
        verdict=verdict,
        suspect_score=suspect_score,
        checks_completed=completed,
        not_applicable=not_applicable,
        checks_infra=checks_infra,
        evidence=ordered,
        isolation=isolation,
        profile=profile,
        infra_reason=infra_reason,
        infra_detail=dict(outcome.infra),
    )


_EXIT_CODES: dict[str, int] = {"PASS": 0, "SUSPECT": 1, "FAIL": 2}


def exit_code(verdict: Verdict) -> int:
    """PASS 0, SUSPECT 1, FAIL 2, and `None` (INFRA_ERROR) 3."""
    if verdict.verdict is None:
        return 3
    return _EXIT_CODES[verdict.verdict]
