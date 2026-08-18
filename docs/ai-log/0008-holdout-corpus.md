# 0008: the blind holdout, tooling and the authoring run

Date: 2026-08-18
PR: #8 (b809ce24d65f, merged b1e39ba). GitHub #8 is ladder task 7; the
numbering diverged early in M6 and the ledger tracks the ladder.
Agent: Claude Code (Fable 5) as controller, one Opus implementer across the
build and both fix rounds, one Opus review of the tooling, one Sonnet
re-review of the fix round. The 18 authoring sessions themselves ran
`gpt-5.6-sol` through codex-cli 0.147.0, packet-only.
Produced: five scripts under `scripts/` (packet builder, leak check, Codex
author runner, screen ladder, transcript audit), 45 tests, and the committed
run under `evals/v1/holdout/` (registry, notes, 18 session records, audits,
18 patches, screen verdicts). The spec's blinding paragraph was amended to
state that the sandbox confines writes and not reads. README pinned to
`@v0.1.1`. Nothing under `skeptic/` changed: `verifier_revision()` held at
`26e0ae21ad5f` through the merge.
Human: Mamadou approved the tooling package and the enforcement choice
(packets outside the checkout, enforced in code, plus a committed-transcript
audit, over a container jail), ran nothing himself, adjudicated all 11
admitted categories from the diffs alone (11 confirmed, 0 relabels), and
reviewed and merged. Authoring was $0 marginal through his Codex
subscription; M6 paid spend stays $0 of $15.
Verification: ruff clean, fast suite 955, no em dashes in the diff. The
tooling took two review rounds; the re-review found two holes in the new
audit script (a `/tmp` exemption that would excuse a sibling packet, a
missing macOS `/etc` alias), both fixed. Three findings surfaced during the
run itself, all disclosed in DECISIONS row 225 rather than smoothed over.
Prediction record. The run's honest dents were caught by controls and the
audit, not by the sessions reporting clean. The screen's first pass ran in
fresh venvs where pygments 2.21.0, released after the corpus gate against
unpinned task installs, reddened eight rich rendering tests on any tree; the
committed `rich-0001-h6` control, green under `seed --check`, screened
tests-red and exposed it, so every verdict was re-produced in the corpus-era
venvs and five re-rolls the contamination triggered were voided. One session
of eighteen searched the disk, found the screen's leftover venvs, and ran the
packet's tests, which the audit reported only after a fix taught it to judge
a symlink's literal path as well as its resolved one. The dependency drift is
an M7 finding: the venv lane does not reproduce on a fresh machine until the
task installs pin their transitive dependencies. Result: n=11 of 12, H10
unrepresented, published with the count.
