# 0009: the blind holdout run

Date: 2026-08-22
PR: #9 (31d36b5, merged 41ddc9e). GitHub #9 is ladder task 8.
Agent: Claude Code (Opus 5) as controller, with an eleven-agent adversarial
workflow over the preflight: six verifiers, one per dimension, and five
skeptics tasked with refuting whatever came back SAFE.
Produced: the paid sweep's snapshot under
`evals/v1/runs/eval-20260822-163818/` (11 rows, manifest, table), the README's
"The blind holdout" section, a figure-trace test deriving every published
holdout number from that run, an extension of the fixed-point test to it, and
DECISIONS row 226. One code change rode in ahead of the spend: `image.py`
gained `live_image_digest` and `_image_id` calls it, moving
`verifier_revision()` from `26e0ae21ad5f` to `a68e984d6206`. The README also
lost its "Working with AI" section and took a trim pass at the owner's
direction.
Human: Mamadou chose to fix the image lock before spending rather than publish
around it, confirmed the paid command, and reviewed and merged. Paid spend
$0.4250 for 11 verdicts, taking M6 to $0.4250 of $15.
Verification: ruff clean, fast suite 958, docker 83 passed and 1 skipped (the
paid-lane test, still gated behind `SKEPTIC_PAID_TESTS`, which is the
structural proof nothing extra was spent). A free deterministic rehearsal of
the same 11 rows ran first and returned 0 INFRA at $0.

Prediction record. Three calls to grade.

The preflight's own image-reuse verdict came back SAFE and the skeptic assigned
to break it refuted it. The no-rebuild half held: both corpus images were
present and the rich one still carries pygments 2.20.0. The half nobody had
checked did not. `_image_id` fell through to a mutable tag on all 11 rows,
because no `result.json` in a live workdir carries the `image_tag` key row 222
requires, and measured before the fix, 0 of 11 manifest entries were digests.
The adversarial pass is what turned a go-signal into a defect.

The rehearsal predicted which rows the paid layer had to earn. Five evaded at
the deterministic profile: H5 twice, H6 twice, and H7, all of them the
source-touching patches. The paid checks lifted four of the five. The one they
did not lift was H7, the category already pre-registered as the weak one.

Cost came in under estimate. Predicted about $0.67 from Eval A's measured
$0.0555 per verdict, actual $0.4250, against a printed sweep maximum of $1.43.

Result: detection lenient 10/11, strict 5/11, attribution top-1 6/11 and
anywhere 11/11, 0 INFRA. Judge-alone caught 11/11 against Skeptic's 10/11 and
scored 0/11 strict against its 5/11, so blind data repeated Eval A's verdict on
the baseline rather than overturning it. The figure-trace test caught the one
prose error before it shipped: "6 of 11" written where the run says "6/11".
