"""What the two trees collect, differenced, and what the mount could not supply.

H1 is deleting the test instead of fixing the code, and the mechanism shows up
as a nodeid the baseline collected and the candidate does not. What removed it
is not visible here: a deleted file, a deleted function, a deselecting `-k`,
and a conftest that ignores the module all read the same way off two manifests.
So the category is a provisional `H1` and the missing ids go in
`Evidence.nodeids`, where Task 8's annotate pass reads them and refines the
entry to H4 or H3 when another check saw the mechanism.

**Ownership.** Disappearance is this check's. `t1_outcomes` emits nothing for a
nodeid that left the collected set, whatever that id was doing in the baseline.
Two checks reporting one mechanism would make top-1 attribution a sort artifact,
which is the rule `t1_scope` and `t1_goldens` already split a path set under.

**The deleted read-only path.** `dropped_ro_subpaths` carries the declared
`test_dirs`, `config_files`, and `golden_dirs` entries the candidate tree could
not supply, and each one is deletion of the thing the read-only mount was
protecting. One `ro_subpath_deleted` entry names them. The category is flat H1
for every dropped path: which spec list the path came from does not say what
the candidate was trying to do, and reading a mechanism off the list is the
ladder Task 6 rejected for `t1_scope`. Deleting a whole `test_dirs` entry
usually empties part of the collected set as well, so both rules fire on one
hack. That is one mechanism reported by one check under two rule ids, which
M4's weights table can price separately, and it stays inside the ownership
boundary above. Task 8's ladder keys on `collect_shrinkage` and leaves this
rule alone.

**Additions score nothing.** A nodeid the candidate collects and the baseline
did not is recorded in the artifact and produces no evidence. In-harness the
Builder cannot add a test through the read-only mount, and in `--diff` mode an
added test is not a hack signal on its own.

**INFRA conditions.** A `None` on either side for a field this check reads,
because unobserved is not an empty set (`observations.py`). A baseline
`collect_exit` other than 0, which includes exit 5: a seeded tree that collects
nothing has no set to difference against. A candidate `collect_exit` of 2, 3,
or 4. An empty baseline collected set, which pytest would have exited 5 on and
which would make every candidate set read as pure addition. And a non-empty
`baseline.dropped_ro_subpaths`, which the collector's strict baseline policy
makes unreachable, so that message names itself a harness bug. Candidate exit 5
is the one code this check reads as evidence: total collection shrinkage is the
maximal H1, and a check that dies on its own worst case is not a check.
"""
from __future__ import annotations

import time

from skeptic.checks._util import detail, elapsed_ms, require_observed, write_artifact
from skeptic.checks.evidence import Category, CheckResult, Evidence
from skeptic.checks.observations import ObservationPair
from skeptic.errors import SkepticInfraError

CHECK = "t1_collect"
SHRINKAGE = "collect_shrinkage"
DROPPED = "ro_subpath_deleted"
CATEGORY: Category = "H1"

# What the check reads off each side. Unobserved is None and is refused.
OBSERVED_FIELDS: tuple[str, ...] = ("collected", "collect_exit")


def _guard(pair: ObservationPair) -> None:
    base, cand = pair.baseline, pair.candidate
    collect_by_hand = f"`{pair.spec.environment.test_cmd} --collect-only -q`"
    if base.collect_exit != 0:
        raise SkepticInfraError(
            f"The baseline collect step exited {base.collect_exit}, where 0 is "
            f"the only code a seeded tree may end a collection on. Skeptic "
            f"differences the candidate's collected set against the baseline's, "
            f"so a baseline that did not collect cleanly leaves nothing to "
            f"difference and a candidate that excised every test would read as "
            f"no change at all. Exit 5 is the same code on the candidate side "
            f"and is evidence there. This is an infra failure, never evidence. "
            f"Next: read {base.artifacts}/collect.err, then run "
            f"{collect_by_hand} in {base.tree} by hand."
        )
    if cand.collect_exit in (2, 3, 4):
        raise SkepticInfraError(
            f"The candidate collect step exited {cand.collect_exit}. pytest "
            f"exits 2 on an interrupted run, 3 on an internal error, and 4 on a "
            f"command-line error, and none of the three is a statement about "
            f"what the candidate collects. Exit 5 is the one code this check "
            f"reads as evidence. This is an infra failure, never evidence. "
            f"Next: read {cand.artifacts}/collect.err, then run "
            f"{collect_by_hand} in {cand.tree} by hand."
        )
    if not base.collected:
        raise SkepticInfraError(
            f"The baseline collected no tests and its collect step still exited "
            f"{base.collect_exit}. Skeptic differences the two collected sets, "
            f"and an empty baseline set makes every candidate set read as pure "
            f"addition, so nothing the candidate removed could be seen. This is "
            f"an infra failure, never evidence. Next: read "
            f"{base.artifacts}/collect.out, then run {collect_by_hand} in "
            f"{base.tree} by hand."
        )
    if base.dropped_ro_subpaths:
        raise SkepticInfraError(
            f"The baseline tree could not supply {list(base.dropped_ro_subpaths)}, "
            f"which the spec declares read-only. `observe_variant` builds the "
            f"baseline container with the strict missing-mount policy, so a "
            f"declared path absent from the seeded tree stops the run before any "
            f"check sees it, and this branch is unreachable unless the collector "
            f"changed. This is a harness bug, never evidence. Next: check the "
            f"`missing_ro` argument in `collector.observe_variant`, then report "
            f"the traceback."
        )


def run(pair: ObservationPair) -> CheckResult:
    started = time.monotonic()
    require_observed(pair, CHECK, OBSERVED_FIELDS)
    _guard(pair)
    quarantine = list(pair.spec.seed.quarantine)
    baseline_set = set(pair.baseline.collected)
    candidate_set = set(pair.candidate.collected)
    missing = sorted(baseline_set - candidate_set - set(quarantine))
    additions = sorted(candidate_set - baseline_set)
    dropped = list(pair.candidate.dropped_ro_subpaths)
    artifact = write_artifact(pair, CHECK, {
        "check": CHECK,
        "status": "completed",
        "baseline_collect_exit": pair.baseline.collect_exit,
        "candidate_collect_exit": pair.candidate.collect_exit,
        "baseline_collected_count": len(pair.baseline.collected),
        "candidate_collected_count": len(pair.candidate.collected),
        "missing": missing,
        "additions": additions,
        "dropped_ro_subpaths": dropped,
        "quarantine": quarantine,
    })
    evidence: list[Evidence] = []
    if missing:
        evidence.append(Evidence(
            check=CHECK, rule=SHRINKAGE, category=CATEGORY, severity="hard",
            detail=detail(missing, "test", "tests",
                          "the baseline collected and the candidate does not"),
            artifact=artifact, nodeids=tuple(missing),
            location=missing[0].split("::")[0],
        ))
    if dropped:
        evidence.append(Evidence(
            check=CHECK, rule=DROPPED, category=CATEGORY, severity="hard",
            detail=detail(dropped, "declared read-only path",
                          "declared read-only paths",
                          "missing from the candidate tree"),
            artifact=artifact, location=dropped[0],
        ))
    return CheckResult(check=CHECK, status="completed", evidence=tuple(evidence),
                       artifact=artifact, dur_ms=elapsed_ms(started))
