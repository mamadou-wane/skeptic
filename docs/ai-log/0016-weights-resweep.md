# 0016: one weight, both sets re-swept

Date: 2026-08-22
PR: #16 (17a4679, merged 4a91683).
Agent: Claude Code (Opus 5).
Produced: `pattern_introduced` 0.4 to 0.75, both eval sets re-run at
`verifier_revision` 42a7253cd318, the README republished, DECISIONS row 229,
and the test fallout.
Human: Mamadou chose to batch the change and sweep once rather than publish
three times, and reviewed and merged.
Verification: ruff clean, fast 977, docker 83 passed and 1 skipped. 53 + 11
pairs, 0 INFRA on both, $3.2317.

Dev detection lenient 27/29 to 29/29, attribution anywhere 28/29 to 29/29,
holdout 10/11 to 11/11, deterministic lane 16/29 to 17/29. Strict and both
false-positive splits did not move. Both H7 rows land SUSPECT at 1.00 and 2.00.

The weight was picked by rescoring committed evidence, which costs no API call,
so every figure was known before a cent was spent. The sweep buys provenance:
rescoring alone would have left committed `verdict.json` files recording PASS
under a README claiming SUSPECT, which is the defect class this harness exists
to catch.

Prediction record. The milestone's discipline paid and one of its claims did
not survive.

H7 stayed unpatched for a whole milestone on purpose, on the rule that a weight
tuned after one miss is tuning to noise. Three independently authored sources
then put it at 0.65 against a 1.0 threshold, and one number closed all three
without moving a false positive.

Against that, judge-alone's false positive vanished. It read 1 of 12 on gold in
August and 0 of 12 on the same twelve clean patches here, because it is a
sampled LLM call. The README had claimed Skeptic holds 0 false positives
against the judge's 1, which read a die roll as a property of the baseline.
Withdrawn rather than swapped for 0 of 12, since two draws is not a rate.
Skeptic now ties judge-alone on lenient recall where it previously lost, and
what survives is the strict column, the evidence trail and reproducibility.

The guard detector from 0015 rode along and contributed nothing measurable. It
fires on `gold-prime`, which returned PASS 0.40 with no guard evidence and no
false positive. The `advtest_divergence` delta of 14 to 15 is sampling noise:
neither gained row removes a guard.
