# 0025: the diff lane's boundary, and two of three real PRs reach a verdict

Date: 2026-08-29
PR: #25 (3cdf1bc, 0d4da76, 9a232a2, 3ab65c9, 2fc617b, merged e474e37).
Agent: Claude Code (Fable 5), with an Opus review of the diff before push.
Produced: `--use-pep517` on the session-start overlay install;
`diffmode.assert_python_project`, the refusal before any container for a
root with neither `pyproject.toml` nor `setup.py`; the exit-4 refusal naming
the extra to install; `action.yml`'s `install` input; `Verdict.infra_detail`
with the run's own root stripped at the seam; every "any repo" claim
replaced by the boundary; the three real PRs re-audited at `f34bc7798a33`
with patches, a manifest and no host path under
`evals/v1/diff-lane/20260829/`, bound by `tests/test_diff_lane_record.py`;
DECISIONS row 235.
Human: Mamadou ruled the scope (support the `setup.cfg` shape, prove
lp-to-jira#16 reaches a verdict, scope no-metadata repos out with an
actionable error, no dependency discovery), then asked that lp-to-jira's
result be stated as the two orthogonal facts it is, and authorized the merge
on green CI.
Verification: ruff clean; full suite as one invocation, 1092 passed and 1
skipped; both CI runs green on the corrected head.

What the re-audit says. watchman#40 PASS 0.25 as on 2026-08-22. nexus#1
refused by name before an image is built, exit 3. lp-to-jira#16, with its
`[test]` extra named on the install line: verdict FAIL 0.00 on one hard
`collect_shrinkage` row, because the committed patch renames a test and
rewrites its assertions and the collect diff records the old id as missing;
and `checks_infra` naming `t1_coverage`, which could not obtain test
contexts. Run status `ok`, exit 2, by the aggregator's own rule that a FAIL
is evidence-only and never consults a non-mandatory infra check. The likely
cause of the coverage failure is pytest-cov's own run under the repo's
`addopts = --cov`; not measured, not fixed, a documented v1 limitation.

Prediction record. Two wrong turns, both caught by the review before push.
The first draft routed a check's refusal text into `verdict.json` verbatim,
and that text names absolute artifact paths, so a committed record carried
this machine's user and a session id: row 220's defect class, reproduced by
the very field added to make the failure readable. `relative_to_run` strips
the run's root at the seam now, and a test asserts no record carries a host
path. The second: the watchman record was produced before `infra_detail`
existed and sat beside lp-to-jira's as if from one revision; all three were
re-run at the final code with a manifest that names it. Two prose claims
also went out ahead of their evidence, the rewritten assertions (now cited
to the committed patch) and the pytest-cov mechanism (now a hypothesis, as
it is).
