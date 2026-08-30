# 0026: M7 closes, and Skeptic is 1.0.0

Date: 2026-08-29
PR: #27 (e121ea9, e3d0f3c, 89e6391, 969fbf8, merged aede5e8), replacing #26,
which GitHub closed when its base branch was deleted on #25's merge.
Agent: Claude Code (Fable 5), with an Opus close-out audit against the
rulings before the branch was opened.
Produced: DECISIONS rows 236 (the rulings) and 237 (the close); the plan
amended in place so Part 2's taxonomy table keeps the line range the holdout
packet pins; the Status paragraph; the version bump to 1.0.0 in
`pyproject.toml` and `skeptic/__init__.py` together; and, after the tag
existed, the README's Action snippet at `@v1.0.0`.
Human: Mamadou ruled on every inherited deliverable (report polish cut,
GIF/PNG cut, H7 closed on rows 229 and 230, §16 amended to the PR #19
split), set the close date to the local day, asked for one release with
every version location moving together and the measured revisions kept
historically accurate, and required a fresh full green CI run on the exact
final tree before the tag.
Verification: ruff clean; full suite as one invocation on the final tree;
CI green on the PR head and again on the merge commit before the tag.

The close. Every M7 criterion is met on its row or on the row that amended
it: 67 s from a fresh clone to a first real verdict (row 232), task installs
pinned to the closures the published runs measured (row 231), the guard
probe's parser fixed and the agent-authored hack caught 3 of 3 in
re-verification (row 230), the judge re-sampled and reported per run (row
234), the diff lane bounded with two of three real PRs reaching a verdict
(row 235), three stale figures corrected and bound (row 233). About $6.04
of paid runs across M7.

On dates and revisions. The rows date by the local day, and the runs of the
evening of 2026-08-29 carry UTC timestamps that read 2026-08-30; the close
is 2026-08-29. Every measurement rows 229 through 237 cite was taken at or
before `f34bc7798a33`, the tree of PR #25; the version commit that follows
moves the revision by the version string alone, as M5's and M6's post-close
fixes did (ai-log 0012), and nothing was measured under it.

What stays open, each with its row: the guard-probe follow-ups (row 230),
`t1_coverage` under a repo's own `--cov` (row 235), a second machine's
footprint run (row 232), and the eight commits of 2026-08-29 on main that
carry a session trailer, left because removing them is a force-push of a
public main.
