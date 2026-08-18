# 0006: eval machinery, and wave A closes

Date: 2026-08-17
PR: #6 (c9ea18b9bb1e53ced77560e3eb6621eede80ebfe)
Agent: Claude Code (Fable 5) as controller; an Opus 5 implementer, an
Opus 5 task reviewer, a Sonnet 5 re-reviewer.
Produced: weights_sha256 in both manifests with the fixed-point test
extended to the published 53-row run and the weights literals pinned; the
holdout registry with sha256, duplicate-pair, and corpus-collision
refusals; verify --variant-patch; the run-dir-only manifest write;
render_arm_comparison with the replayed and estimated caveats carried
over; arm-manifest knob provenance; a byte-match pin for the published
table.
Human: Mamadou reviewed and merged all four of the day's code PRs,
closing wave A in one sitting.
Verification: ruff clean, fast suite 893, docker suite 83 passed / 1
skipped, evals/v1 byte-untouched, the base arm's comparison output checked
against the committed arm.md. With this merge the M6 code is done and the
verifier revision the paid runs will publish under is 26e0ae21ad5f.
Prediction record. The task review found three defects that would each
have corrupted a published number only after money was spent: duplicate
registry rows silently collapsing the holdout denominator, a registry id
colliding with a corpus variant so a hacked row would score as clean off
the spec's label, and the arm comparison quoting replayed historical cost
beside fresh cost with no marker. The fix round also surfaced a
pre-existing guard test that passed vacuously because every bad input hit
a different refusal with the same asserted strings. The eval lane's whole
premise is that green output is not evidence of correctness; its own
tests keep proving the premise.
