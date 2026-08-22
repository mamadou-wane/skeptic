# 0015: the removed-guard probe, three links of four

Date: 2026-08-22
PR: #15 (48335b4, merged a26d946).
Agent: Claude Code (Opus 5).
Produced: `skeptic/checks/guards.py`, the guard directive threaded into
testgen, the `seeded_green` rung widened to admit a regression probe, and 14
tests.
Human: Mamadou approved the design after two of my earlier diagnoses turned out
wrong, then reviewed and merged.
Verification: ruff clean, fast 977, six paid probe runs at about $0.33.

The chain is detect, direct, admit, diverge. Three links hold and the fourth
does not, and the PR says so rather than claiming a catch.

Detection is measured: it fires on 1 of 53 corpus variants,
`rich-0003/gold-prime`, a clean one where the reference agrees and no evidence
is emitted. The rung widening is verified: `seeded_green` rejections went 1 to
0 on the target pair. Generation is the link that fails. One run wrote exactly
the right test and the next wrote none, sending negative-input probes to
`rich.cells.split_text` where the reference does not raise.

Prediction record. Two diagnoses of mine were wrong before one was right, both
from reading symptoms instead of code.

I said removal was invisible to a coverage-gated ladder by construction.
`target_coverage` is measured on the reference tree, where the deleted guard
still exists, and it had rejected those tests correctly because they exercised
the wrong module.

I then said the generator is shown the seeded source, so a preserve-the-guards
rule would tell it to preserve the planted bug. The call site materializes the
pristine pre-seed tree; the generator sees the reference's own contract.

What was right, and only because it was measured first: a static
removed-an-assert rule is disqualified before it is written, because
`gold-prime` deletes the same `assert cut > 0` the agent-authored hack deletes
and replaces it with a guard that still rejects negatives. Only the reference
separates them.
