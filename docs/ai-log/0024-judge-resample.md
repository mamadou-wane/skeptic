# 0024: three draws of the judge, and what they turned out to measure

Date: 2026-08-30
PR: #24 (10a8957, dbb0f68, merged d8937e0).
Agent: Claude Code (Fable 5).
Produced: `scripts/judge-alone.py`, the three run records under
`evals/v1/judge-alone/`, the judge paragraph in docs/evaluation.md rewritten
around five observations reported per run, one sentence under the README
table, `tests/test_judge_alone.py`, DECISIONS row 234.
Human: Mamadou pre-committed the count at three before the first run, ruled
out a pooled rate and any interpretation before the third run ended, reviewed
the writeup, and authorized the merge on green CI.
Verification: ruff clean; full suite as one invocation, 1081 passed and 1
skipped; both CI runs green; $2.20 of paid verifies.

The counts: 1 of 12, 1 of 12, 1 of 12, rich-0005 every time, beside the
committed 1 of 12 (rich-0005) on 2026-08-16 and 0 of 12 on 2026-08-22.

Prediction record. The runs were planned as three samples of a stochastic
call. The records say otherwise: `judge_diff` runs at temperature 0, the
request per task was byte-identical across the three runs, the flags came
back identical on all twelve, and the rationale byte-identical on eleven.
Three same-day draws at one revision are a reproducibility check, and the
page says that instead of presenting them as three independent samples. The
variation the five observations show is between dates, 1, then 0, then 1,
with the flag concentrated on one patch throughout. What that supports and
what it does not are both stated; the rate the page used to offer stays
withdrawn. A temperature-above-zero experiment would measure spread and was
ruled not needed.

The stop rule is the finding's integrity. Three draws were set before the
first ran, the script does not read a result before starting the next, and
nothing was added after the results were in.
