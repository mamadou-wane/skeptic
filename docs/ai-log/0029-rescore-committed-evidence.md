# 0029: rescoring the committed evidence

Date: 2026-09-01
PR: opened from branch `eval/scoped-rescore`; number assigned on open
Agent: Claude Code (Fable 5.1). A 30-agent review preceded the work: seven
subsystem maps, six Opus proposers under different lenses, one merge pass,
one adversarial verifier for each of the twelve merged proposals, three
judges and a completeness critic. A four-agent review followed it: one
recomputed all 399 published cells from the raw files with zero mismatches,
one fact-checked the prose and found eleven sentences to correct, all
corrected, one reviewed the code, one checked the writing rules.
Produced: `scripts/rescore-scoped.py`, `tests/test_rescore_scoped.py`,
`changed_lines`, `variant_patches`, `baseline_size_only` and a `category`
field on `EvidenceRule` in `skeptic/evalkit.py` with their tests, the
"Rescoring the committed evidence" section of docs/evaluation.md, two rows
and one paragraph in the README's Evaluation section, DECISIONS row 241.
Human: Mamadou asked what would take the project to the next level, chose
the order "rescore the committed evidence, then repeat the paid sweep, then
the merged-PR flag rate", and approved this first step.
Verification: ruff clean; 1109 passed under `-m "not docker and not slow
and not paid"` against 1095 before, the 14 new tests included; the headline
figures were recomputed from the raw `verdict.json` files, patches and
registry by a throwaway script before `scripts/rescore-scoped.py` existed,
and the fixed-point tests carry those values.

What was done. Nothing was re-run and nothing was paid for. The two committed
runs the headline stands on, `eval-20260822-195147` and
`eval-20260822-211836`, were rescored four ways under the shipped weights:
the strict column split by the plan's Control column, a baseline that reads
only the patch's line count, a per-rule table of fire counts and pivotality,
and a leave-one-category-out table under three postures. The script prints
the tables, docs/evaluation.md quotes them verbatim, and the tests bind the
script, the doc and the README together.

What the tables say. Every hard-rule FAIL is on a category the sandbox
prevents; strict on H5 through H8 is 0 of 17 and 0 of 6. A rule that flags
any patch over 4 changed lines scores 19 of 29 at 2 of 12 gold-prime, above
the deterministic lane's 17 of 29, because the hacked patches run to 120
lines at a median of 7 while every gold fix is 2 lines and no gold-prime
exceeds 10. Two soft weights never fired; `judge_flag` fires on every hacked
row and is pivotal on 3 of 40. Removing every evidence row labeled with a
category leaves 24 of 40 catches in-harness and 0 of 19 in the free lane
once `scope_violation` goes too, every survivor carried by `scope_violation`
or by `advtest_divergence`'s fixed H6 label.

What it costs. The README's headline now sits next to a baseline row the
free lane does not beat on detection, diff-size >4 lines at 19/29 against
17/29, which pays 2/12 on gold-prime to get there, and a paragraph that says
where the strict column comes from. That is the intended trade: a claim a
reviewer can take apart in a minute is replaced by one they cannot.

Left open, on row 241: a rule-ownership fold for leave-one-category-out,
which needs a hand-authored mapping from category to primary detector;
size-matched clean controls, refactor-scale patches that keep behavior; and
the repeat of the paid sweep that would put an interval on 29/29. The last
two are the next steps and need new runs.
