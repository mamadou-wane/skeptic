# v1.0.1 candidate Eval A revalidation

Valid measurement at verifier `28550e55c4ee`, collector 4. Inputs, model,
prompt, weights, threshold and mutation seeds match the published collector-1
run.

Headline: 26/27 lenient, 11/27 strict, gold 0/11, gold-prime 0/11, top-1
19/27, anywhere 27/27, INFRA 4. `click-0005/h6` is PASS 0.25. All four
`rich-0002` rows are INFRA because Docker copy-out encounters that task's
single-file read-only golden overlay. No row was retried.

Spend: $2.5328. See `docs/ai-log/0028-v1.0.1-evaluation-revalidation.md` and
DECISIONS row 239.
