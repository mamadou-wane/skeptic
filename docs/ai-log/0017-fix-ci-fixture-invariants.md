# 0017: CI caught what a half-run suite did not

Date: 2026-08-22
PR: #17 (fbf9386, merged 5faa63d).
Agent: Claude Code (Opus 5).
Produced: the two fixture-invariant tests rewritten around what the weight
change made true, and DECISIONS row 229 amended with both the design change and
the process error.
Human: Mamadou reported the red CI and reviewed and merged the fix.
Verification: ruff clean, full suite 1060 passed and 1 skipped, run as one
invocation rather than in two marker-filtered halves.

Prediction record. This one is a process failure with a specific cause, worth
writing down because the fix is a habit rather than a patch.

Row 229's sweep edited `DETERMINISTIC_VERDICTS`, a table both halves of the
suite read. I then re-ran only the docker-marked half, twice, and reported
gates green off that. The two tests that read the table as data carry no
marker, so `-m docker` deselected them both times, and the last `-m "not
docker"` run predated the edit. Splitting a suite by marker is fine; reporting
it green when the halves were run against different trees is not. After
touching anything both halves read, run the whole suite the way CI does.

The substance was not a stale literal either.
`test_wave_a_h5_h6_h7_score_strictly_between_zero_and_suspect_in_the_diff_
posture` pinned a real design property: h5, h6 and h7 all score under
`SUSPECT_THRESHOLD` in the diff posture, and the paid `advtest_divergence` is
the only flip past it (decision 114). At `pattern_introduced` 0.75 that stopped
being true for h7, whose fixture carries a soft coverage row as well and now
sums to 1.15 with no paid check at all. The test says which rows still sit
under the ceiling and which one crosses it, rather than having its expected
verdict edited to match. It does not generalize to the corpus, where the two
real H7 rows carry no coverage row and the deterministic lane still reads 0 of
2, and the clean splits stayed 0 of 12 and 0 of 12.
