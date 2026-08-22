# 0012: closing M6

Date: 2026-08-22
PR: #12 (9a26216, merged 85565fb). GitHub #12 is ladder task 10.
Agent: Claude Code (Opus 5).
Produced: the README status rewrite, the plan §14 schedule note, DECISIONS row
228 walking the three exit criteria, and the version bump to 0.2.0 across both
`pyproject.toml` and `skeptic/__init__.py`.
Human: Mamadou delegated the close and reviewed and merged it.
Verification: ruff clean, fast suite 959, docker 83 passed and 1 skipped (the
paid-lane test, still gated behind `SKEPTIC_PAID_TESTS`). WEIGHTS and
SUSPECT_THRESHOLD never moved this milestone, which is what keeps the holdout
and the dev set comparable.

M6 owed three things and delivered three. The holdout row is in the table at
10/11 lenient and 5/11 strict. Per-arm incidence is published at 0 of 6, 1 of 6
and 0 of 6. The Action demo is recorded, and what it records is three
INFRA_ERRORs on three real public agent PRs. Two of the three produced results
that make the harness look worse than M5's did, and the milestone closed on them
anyway, which is the whole reason the criteria were pre-registered as recorded
rather than passing.

Ledger: $4.1979 of a $15 ceiling, inside the spec's expected $3-6 and under the
$10 check-in. Every measurement was taken at `verifier_revision` a68e984d6206
and its manifests record it.

Prediction record. The version bump caught something nobody was looking for.
`skeptic/__init__.py` had `__version__` hand-pinned at 0.1.0 while
`pyproject.toml` said 0.1.1, so `skeptic --version` under-reported itself for
the entire v0.1.1 Marketplace release, and a close that bumped only pyproject
would have shipped a v0.2.0 tag whose CLI still said 0.1.0. Fixing it moved the
live revision to a75040380491, after both paid runs had published and recorded
a68e984d6206 in their own manifests, the same shape as ff7c236 landing after
M5's close. Nothing published moved: a manifest records the revision its own
run used.

M7 inherits four items M6 named rather than fixed, each recorded where it was
found: the H7 rule, now with three measurements asking for it instead of one; an
arm snapshot that carries its own candidate diff; the diff lane's
install-and-apply path against arbitrary repos; and task installs that pin their
transitive dependencies.
