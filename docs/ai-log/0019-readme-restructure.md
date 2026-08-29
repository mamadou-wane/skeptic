# 0019: the README becomes a front page

Date: 2026-08-26 (entry written 2026-08-29).
PR: #19 (cb13c89, merged 60a685a).
Agent: Claude Code (Fable 5).
Produced: README.md cut from 631 lines to 190. `docs/evaluation.md` (350
lines) and `docs/architecture.md` (193 lines) carry what moved, near-verbatim.
Three drift tests repointed at docs/evaluation.md and two new ones binding what
the README still publishes. Both new docs added to holdout-leakcheck's
`WITHHELD_FILES`. Five stale post-resweep figures fixed in the move.
Cross-references in guards.py, judge.py and the rescore script updated.
Human: Mamadou reviewed the split before push and merged it the same evening.
Verification: ruff clean; fast suite 979 passed and 84 skipped as one
invocation, the skips being the docker-marked half; CI green on cb13c89 and on
the merge commit; the new table test shown to fail on a mutated cell and pass
on restore.

This departs from the plan's definition of done. §16 of
`docs/skeptic-engineering-plan.md` has the README lead with the full eval
table with baselines, attribution, cost actuals, the footprint anchor, the
architecture diagram, the tradeoffs, the limits section and related work.
After #19 the README carries one merged table (dev and holdout, lenient and
strict, FP on gold and gold-prime, Skeptic against its three baselines), the
demo, how it works, the CI audit, the roadmap and a Documentation section.
Attribution, cost actuals, the lanes table, the pressure arms, the holdout
writeup and the agent-authored hack sit in docs/evaluation.md; the diagram,
tradeoffs, limits, related work and layout in docs/architecture.md. One click
away is not "leads with". §16 stays as written and this entry is the record,
not an amendment. The reason is the plan's own audience line: a reviewer with
sixty seconds to judge credibility does not read 631 lines. The front page now
fits that budget, and every figure on it is still bound to a committed run.

Prediction record. The first draft of the new README-table test passed three
mutations in a row: the always-SUSPECT FP cells edited 12/12 to 3/12, its
strict cells edited 0/29 to 10/29 (a search for `0/29` is satisfied by
`10/29`), and the Skeptic and judge-alone rows swapped wholesale. It checked
that each figure appeared somewhere in the section, which the section always
satisfies because the figures repeat across rows. The rewrite reconstructs each
of the four rows from the two committed runs and matches the whole row line,
which catches an edited cell, a collision and a swap. Mutate the file before
trusting a drift test, the way 0017 says to run the whole suite before trusting
a green.

The five figures fixed in the move were all drift left behind by the row 229
re-sweep: Status 10/11 now notes 11/11; Tradeoffs 16/29 vs 27/29 now 17/29 vs
29/29; the CI caveat's worst-case bound 16 to 14 now 17 to 15; H5 1 of 6 now 2
of 6; the rescore script docstring 27/29 now 29/29. A sentence-level scan of
the old README against the three new files found 27 of 238 sentences not
present verbatim. The lanes table, the cost actuals and the Builder-as-oracle
explanation it flagged were moved with rewording, and the dated 2026-08-17
demo capture stays in the README.

Still uncited: the SWE-bench 120 GB / 15-50 min line. The M7 footprint table
is the fix.
