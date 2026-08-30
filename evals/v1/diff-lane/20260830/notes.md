# The three real agent PRs, re-audited 2026-08-30

Same three merged `app/copilot-swe-agent` pull requests as the 2026-08-22
audit, same base commits, deterministic lane, $0, all three in one fresh
workdir at verifier revision `f34bc7798a33` (`manifest.json`
carries the base and head shas, the patch digests and the install line per
PR; the patches themselves are under `patches/`, taken with `gh pr diff`).
Command per PR: `skeptic verify --diff <patch> --repo <clone at base> --base
<base> [--install <line>]`.

| PR | install line | outcome |
|---|---|---|
| `EinDev/watchman-pairing-assistant#40` | default | PASS at 0.25, 6 checks completed, 0 infra |
| `hkhonming/lp-to-jira#16` | `pip install -q -e .[test]` | FAIL at 0.00: `collect_shrinkage` (H1, hard) on `tests/test_milestone_sync.py`; `t1_coverage` infra |
| `AlexanderAlcazar/nexus_student_hub#1` | default | refused before any container: unsupported project, exit 3 |

`lp-to-jira#16` with the default install line stops one step later than it
did on 2026-08-22: the overlay install now succeeds, and the baseline collect
exits 4 because the repo's `addopts = --cov` needs pytest-cov, which its
`[test]` extra provides and the default line does not install. The refusal
names the fix. With the extra, the verdict above: `patches/lp-to-jira-16.diff`
renames `test_sync_milestone_to_jira_add_to_existing` to
`test_sync_milestone_to_jira_overwrite_existing` and rewrites its assertions,
and `t1_collect.json` records the old id as missing and the new one as added.
Its one infra check is `t1_coverage`, and `verdict.json` carries why:

```
SkepticInfraError: coverage infra failure: every one of the 0 context strings the run recorded is empty. The pinned rc sets `dynamic_context = test_function`, so a run that honored it records one context per test somewhere. Nothing anywhere carrying a name means the setting was not honored, and then no line in the report can be told from a line that ran at import. A patch whose own lines lack a test context while the rest of the run has them is the H9 hard fail, and this is not that. A coverage number Skeptic cannot stand behind is an infra failure, never evidence: a silent 0% fails a gold patch and poisons the published false-positive rate. Next: check `dynamic_context` in collect/artifacts/candidate/coveragerc and the coverage version in the image, then re-run the pair.
```

The likely cause, not measured here: the repo's `--cov` loads pytest-cov,
which starts its own coverage run, and the pinned rc's `dynamic_context` is
not what that run honors. Not fixed; the verdict stands on the hard row.

`nexus_student_hub#1`'s refusal text is in `nexus_student_hub-1/refusal.txt`.
