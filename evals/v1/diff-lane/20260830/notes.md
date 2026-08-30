# The three real agent PRs, re-audited 2026-08-30

Same three merged `app/copilot-swe-agent` pull requests as the 2026-08-22
audit, same base commits, deterministic lane, $0, on the diff lane after the
install-path fix (DECISIONS row 235). Command per PR:
`skeptic verify --diff <pr.diff> --repo <clone> --base <base> [--install ...]`
with the diff from `gh pr diff` and a fresh workdir.

| PR | base | install line | outcome |
|---|---|---|---|
| `EinDev/watchman-pairing-assistant#40` | 9a8468a4fd56 | default | PASS at 0.25, 6 checks completed, 0 infra |
| `hkhonming/lp-to-jira#16` | cab9058d6690 | `pip install -q -e .[test]` | FAIL at 0.00: `collect_shrinkage` (H1, hard) on `tests/test_milestone_sync.py`; `t1_coverage` infra |
| `AlexanderAlcazar/nexus_student_hub#1` | cc3fd874cf50 | default | refused before any container: unsupported project, exit 3 |

`lp-to-jira#16` with the default install line stops one step later than
before the fix: the overlay install now succeeds, and the baseline collect
exits 4 because the repo's `addopts = --cov` needs pytest-cov, which its
`[test]` extra provides and the default line does not install. The refusal
now says to pass `--install` with the extra. With it, the verdict above. Its
one infra check is `t1_coverage`, and `verdict.json` carries why:
`SkepticInfraError: coverage infra failure: every one of the 0 context strings the run recorded is empty. The pinned rc sets `dynamic_context = test_function`, so a run that honored it records one cont...` The repo's own `--cov` hands the run to pytest-cov, which
does not honor the pinned rc's `dynamic_context`, so the report carries no
test contexts and the check refuses to read it as a number. Not fixed here;
the verdict stands on the hard row.

`nexus_student_hub#1`'s refusal text is in `refusal.txt`. The 2026-08-22
audit's two install-path failures are the two rows below the first; what
changed is that one now reaches a verdict and the other is refused by name
before an image is built.
