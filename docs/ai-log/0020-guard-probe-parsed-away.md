# 0020: the guard probe was parsed away

Date: 2026-08-29
PR: #20 (0e003f0 and 01ff339, merged 951db28).
Agent: Claude Code (Fable 5).
Produced: `guards.directed_probes` and the `guards` argument on
`parse_candidates`, five tests, three committed re-verification runs of the
agent-authored GREEN-wrong under `catch-rate/reverify-20260829/`, the
rewritten agent-hack paragraph in docs/evaluation.md, DECISIONS row 230.
Human: Mamadou said proceed on the M7 list, reviewed and merged.
Verification: ruff clean; full suite as one invocation, 1067 passed and 1
skipped; six paid verifies at $0.28; CI on the branch and on the merge commit.

Prediction record. PR #15 closed on "generation is the link that fails", and
0015 repeated it. Both were wrong, and the evidence that says so was on disk
the whole time: the io artifact of the run that "wrote none" holds nine fenced
blocks, and the ninth is the probe, `pytest.raises(AssertionError)` around
`Segment("hello").split_cells(-1)`. The count coda asks for exactly eight
files and the guard coda for one more, the model obeys both, and
`parse_candidates(text, 8)` drops the ninth. Reading the model's response
before diagnosing the model would have found it in August; reading the
ladder's summary instead took `generated: 8` for the model's count.

The second wrong turn was mine, today. The first fix admitted one block past
the count, on the reasoning that "exactly n" plus "one more" means n + 1.
Measured at 2 of 3: the miss returned eleven blocks with the probe eleventh,
and a second run returned eleven with the probe ninth. The model does not hold
to the count, and two of those eleven were fenced prose with no test function,
which the generation rung rejects and which still take slots first. A cap at
any n + k is a bet on where the probe lands, so the shipped fix keys on
content: past the count, a block is admitted if it carries a directed function
name under `pytest.raises`, the two things the directive asked for, at most
one per guard. Three runs under it read SUSPECT 1.00 on `advtest_divergence`,
one with the probe ninth of nine.

What did not move: the published catch rate. The committed 2026-08-22 verdict
still says PASS 0.00 with no evidence, the drift test still pins it, and
docs/evaluation.md still reads 0 of 1 and 0 of 4. The re-verification is a
later measurement of the same patch at a later verifier revision and is filed
beside the original. Restating a miss as a catch because a later run caught it
is the move the pinned test exists to refuse.

Open from this one: the directive names the function bare, off the AST's
innermost `def`, so `split_cells` reaches the model without its class;
`t2_advtests_io.json` still records the base prompt without the per-call
codas; and a dedicated call for the probe alone stays the fallback if the
content match ever misses.
