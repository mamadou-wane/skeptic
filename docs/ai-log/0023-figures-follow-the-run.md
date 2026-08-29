# 0023: three figures that survived a re-sweep unbound

Date: 2026-08-29
PR: #23 (one commit, merged 5b02652).
Agent: Claude Code (Fable 5), from an Opus close-out audit of M7.
Produced: the paid lane's cost and median corrected to the published run
($0.0555 to $0.0514 per verdict, median 88 s to 93 s), the judge-alone
category attribution corrected (28 of 29 to 29 of 29), the lanes' demo row
tied to the footprint record, the Status paragraph without the arm-snapshot
item PR #13 closed, one test docstring, and
`test_evaldoc_lanes_and_judge_attribution_follow_the_published_run`, which
derives all four figures from the committed sources. DECISIONS row 233.
Human: Mamadou reviewed, agreed the page should carry the latest published
measurement even where it strengthens the baseline against Skeptic, and
authorized the merge on a green full CI run. Docker on the measuring machine
was occupied by the three pre-committed judge-alone runs, so the full suite
ran on CI rather than locally, and the merge waited for it.
Verification: ruff clean; fast lane 994 passed locally; both full CI runs
green, 19 minutes each.

The audit's method found these where the drift tests could not: it traced
three numbers to their committed source by hand and found the source was the
run row 229 had superseded. Every figure a test already read moved with the
re-sweep; these three had no reader. The correction to 29 of 29 is the one
worth noting. The old figure understated the judge baseline in the sentence
that compares it with Skeptic's top-1 21 of 29, and the fix makes the
comparison less flattering. That is the direction the house rule points.
