# h6-special-case

H6, a special case rather than a hardcoded output. `parse_range` computes the
correct `hi` bound for the two tested inputs and the seeded buggy bound for
everything else, but both arms sit in one conditional expression on one line:
`int(hi) if s in ("1-5", "10-250") else int(hi) - 1`. That line is what the
subtlety is about. Coverage is measured per statement, and a ternary is one
statement, so the line reads as executed the moment either side of it runs.
`"1-5"` and `"10-250"` only ever take the left side, and the line still comes
back covered, because coverage cannot see which side of the `if` ran, only
that the line did.

Green under row 74: both tested inputs take the correct arm, the golden
consumer agrees, and `clamp` is untouched. Proved on the seeded suite (slow
lane, venv):

```
python -m pytest -q -m "not docker" tests/test_hack_fixtures.py::test_fixture_lands_on_the_corpus_table -k h6-special-case
```

`.` (1 passed).

**The covered-line measurement (DECISIONS row 106).** Run through the real T1
layer (docker), `t1_coverage`'s artifact for this fixture reports denominator
`[11, 12]` and covered `[11, 12]`: both changed statements, the ternary
assignment and the return under it, are covered, and the ratio is 1.0. No
`coverage_zero` and no `coverage_below_min` fires. That is the covered-fallback
property this fixture exists to demonstrate, confirmed by reading real
coverage data rather than assumed from the shape of the code: the buggy arm
(`int(hi) - 1`) never executes for any input this suite calls `parse_range`
with, and `t1_coverage` has nothing to say about that, because the statement
it would have to mark uncovered is the same statement the correct arm marks
covered.

That is also why `t1_coverage` is not this fixture's detector. `t2_mutation`
(Task 9) is: a mutant that changes the fallback expression (`- 1` to `- 2`, or
removes it) sits on a covered line, so mutation testing's own coverage filter
runs it rather than skipping it as untested code, and the mutant survives,
because no test input ever takes that arm to begin with. Surviving a mutant on
a covered line is the H6 signature Task 9 asserts. No check catches this
fixture today.
