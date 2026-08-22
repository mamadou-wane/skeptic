# 0013: the diff lane's first real verdict

Date: 2026-08-22
PR: #13 (4fb5a94, merged f7c7e1a).
Agent: Claude Code (Opus 5).
Produced: the CRLF fix in `skeptic/candidate.py`, `_snapshot_candidate` in
`skeptic/cli.py`, four tests, and the README's diff-lane paragraph rewritten
around the new result.
Human: Mamadou picked the free block off the M7 slate and reviewed and merged.
Verification: ruff clean, fast 963, docker 83 passed and 1 skipped.

`EinDev/watchman-pairing-assistant#40` had returned INFRA_ERROR three times. It
now returns PASS at score 0.25 with one soft `mutation_caller_control` row and
0 infra, which is this project's first verdict on a real public agent PR.

Prediction record. I diagnosed this one wrong first and the diff says so. From
the symptom, a patch that applied to a clone at base and then failed
`git apply --check` against Skeptic's own materialized tree, I guessed a
double-apply ordering bug. It was encoding: `subprocess.run(text=True)` decodes
`git diff` with universal newlines and `str.splitlines()` terminates on a lone
CR as well, so a CRLF repo's candidate re-extraction dropped the CRs and the
patch stopped matching the file it came from. Reading the bytes instead of the
symptom would have been faster.

The arm-snapshot half was exactly what row 227 described: `snapshot_run`'s
artifact list is the VERIFY set and a BUILD directory has none of those files,
so nothing ever copied `candidate.diff`.
