# 0004: pressure-arm knobs

Date: 2026-08-17
PR: #4 (745812c47acf810dd321dcac18c07e4d9d20c626)
Agent: Claude Code (Fable 5) as controller; a Sonnet 5 implementer from a
controller brief, an Opus 5 task reviewer, a Sonnet 5 re-reviewer.
Produced: the four build-arm override knobs (--max-iterations,
--token-budget, --cost-ceiling, --statement-mode) applied at one site via
spec model_copy so the cache key, budget enforcement, and Builder prompt
cannot drift; bounds guards; the pinned make-tests-pass literal; the
cache-separation and through-build tests.
Human: Mamadou reviewed and merged.
Verification: ruff clean, fast suite 797, cache keys byte-identical with
no flags, checks/ untouched.
Prediction record. The implementer's first suite was green while the one
line the PR exists for was deletable: the task reviewer removed the
override application in build(), 61 tests stayed green, and with it gone
every pressure attempt would have silently replayed the base arm's cached
BUILD results into PR 9's published table. The fix added tests that fail
on that exact mutation, and the re-reviewer re-ran the mutation
independently before verdicting. The reviewer also ruled the knobs
surfacing on `skeptic build` the right trade and flagged that the arm
manifest records nothing distinguishing an overridden arm, which is now a
sequencing constraint: the manifest work lands before any arm runs.
