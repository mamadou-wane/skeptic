# M5 design: publishable core (corpus, evals, demo, README v1)

Date: 2026-08-02. Status: approved by owner (brainstorm 2026-08-02); feeds the wave A implementation plan.

## Context

M4 closed 2026-08-02: wave B merged to `main` at 07a3b9b, all four exit criteria met, live paid runs measured at $0.1185 total. Plan §14 makes M5 the ship point: the repo is publishable as-is if everything after it slips.

What exists: two admitted tasks (click-0001, rich-0001) carrying gold and gold-prime only, `acceptance_tests: null` in both, no hack variants anywhere in `patches/`, no evalkit, no demo, and a CLI of exactly `seed`, `build`, `verify`. What M5 owes per plan §14: a 12-task corpus with gold-primes and acceptance suites, Eval A dev set with baseline rows, Eval B base arm, `skeptic demo`, and a README v1 whose numbers are real.

The inherited problem is measured, not folklore. Across the four 2026-08-02 real-task paid runs: 32 adversarial candidates requested, 19 generated, 2 trusted. Three causes with distinct fixes:

1. Generation shortfall (13 of 32 never existed): both gold-prime runs returned 1-2 fenced blocks against byte-identical prompts whose gold runs returned 8 of 8. Sampling variance, not truncation (the gold-prime runs' out_tok ran ~9% of the 16000 cap; the 8-block gold runs ran 36-40%). `parse_candidates` treats a shortfall as fine and there is no retry.
2. Reference-rung kills (11 of 19): the model asserts edge behavior it cannot verify from static source. rich's whole prompt is 1,772 input tokens (rule.py alone), so Console semantics are guesswork.
3. seeded_green kills (6 of 19, all rich gold): assertions too weak to trip a 2-character width bug, so the candidate passes the seeded tree and proves nothing.

So H5/H6 detection on real repos is unmeasured: the only run with trusted candidates (click gold, 2) is a clean variant where zero divergence is the correct answer.

One defused landmine, recorded so nobody re-litigates it: row 73 (click's BUILD container lacks `less`; 24 pristine tests fail there) does not poison Eval B. Row 74's differential green puts those failures in the pre-Builder baseline of every attempt, and they cancel.

## Shape: tracer bullet, two waves

Wave A proves the entire pipe on the two existing tasks: acceptance machinery, hack variants, the yield fix, evalkit, and a real Eval A mini-table at n=2. Wave B scales it: ten new tasks on the proven template, the full eval runs, demo, README v1.

The reasoning: corpus authoring is the long pole (measured: 3 dedicated commits and a 119-190 line admission report per task; rich's candidate-selection sweep additionally burned 7 measured candidates to keep 1), and authoring ten tasks against an acceptance-suite convention that has never executed once turns any template flaw into ten-task rework. Wave A retires that risk on two tasks first.

M4 authored each wave's plan only when the prior wave's actuals existed. M5 keeps that: one milestone spec (this document), one implementation plan per wave, wave B's authored at wave A exit carrying wave A's measured actuals.

## Scope

In, wave A:

- Task-spec schema, additive on v1 (row 98 precedent, no version bump): the dead `acceptance_tests: str | None` stub is replaced by the plan Part 3 `acceptance_suite` block (`path`, `must_pass_on`, `must_fail_on`), defaulted. Placement follows the plan's layout: the block is a top-level sibling of `evaluation`, and the nested stub under `EvaluationSpec` is removed; both task yamls migrate in the same commit. Hack variants need no schema work (`VariantSpec` already carries `label: hacked` + `hack_category`).
- Acceptance suites at `acceptance/<task-id>/` as pytest files, executed by a new seedcheck invariant (plan invariant 5): pass on pristine, gold, gold-prime; fail on seeded.
- Invariant 4 automation: gold and gold-prime run full VERIFY (deterministic profile, free, cache-aware) as a seedcheck-driven gate.
- seedcheck quarantine exclusion (today a quarantined flaky test can non-deterministically fail its own re-admission).
- Retrofit of both existing tasks: acceptance suite + 2-3 hack variants each. Gold-primes already exist for both (M4 wave A); the owner ratifies rich-0001's cosmetic prime with its documented weakness standing, so the FP-split denominator stays whole at n=12. The retrofit commit also fixes rich-0001.yaml's stale citation: the render sweep lives at docs/admission/rich.md:60-61, not 151-167.
- Yield lane: the five levers in §"Yield lane" below, iterated on the two real tasks, prompt frozen against a measured exit bar.
- Evalkit as a pure disk reader + an eval driver that snapshots per-run outputs; `write_manifest` gets its first production caller; mini Eval A table at n=2 with all three baselines.

In, wave B:

- Ten new tasks: 5 click, 5 rich, one rich task seeded in the card-render path with `golden_dirs` set (the corpus's only real H10 task). Rich's rejected-candidate table (Bar end clamp, filesize zero-size, Columns padding, Bar boundary) seeds the sweep. Admission gains a "admits a materially different correct fix" screen so new gold-primes stay real.
- Hack variants across the 12-task corpus, ~29 diffs at 2-3 per task: h5 x6, h6 x6 (every task carries one of the two detection-claim classes), h7 x3, h8 x3, h1 x2, h2 x2, h3 x2, h4 x2, h9 x2, h10 x1. Category assignment follows each task's bug shape (branch-conditional seeds invite h6, literal-output seeds invite h5).
- Eval A dev-set run over the corpus, paid profile, frozen testgen prompt; weight tuning on the dev set; weights frozen after tuning (the M6 holdout never sees a tuning iteration); the pre-registered bar (>=85% lenient at <=10% FP) checked and published either way, a miss with a post-mortem over a suspicious pass.
- Eval B base arm: 12 tasks x 2 Builder attempts, attempt id salted into the build cache key with per-attempt build dirs (today attempt 2 replays attempt 1 from cache), Builder prompt caching landed first (~90% repeated-prefix input savings, earmarked for exactly this milestone at M2), every attempt classified RED / GREEN-wrong / GREEN-correct / INFRA_ERROR by the acceptance runner.
- `skeptic demo`: minirepo bundled into `skeptic/fixtures/` (shared with self-tests per DX1), pytest becomes a runtime dependency, two verdicts back to back: gold PASS then h1-excision FAIL. Keyless, dockerless, network-free, `Cost: $0.00`, under 60 s.
- Verdict color via `typer.secho` (zero new deps, click handles NO_COLOR/tty); `skeptic tasks` lists the corpus.
- README v1 with real numbers (§"README v1").

Out, with owners:

- `skeptic doctor`: M6. The README's existing "doctor is M6" line was drift with no decision behind it; this brainstorm ratifies it and the DECISIONS row makes it real.
- `runs list` / `report`: M6, with doctor.
- Reference-feedback repair round for testgen: deferred. It widens the input boundary from "pristine source text" to "pristine behavior", a contract extension M5 does not need unless the in-scope levers miss the bar.
- 2x rerun-before-flag flake mitigation: stays hand-quarantine through M5 unless a flake bites during the dev-set runs; either way a DECISIONS row records the state.
- Blind holdout, pressure arms, `verify --diff` + Action: M6. GIF/PNG, timed fresh-clone: M7.

## Exit criteria

Wave A:

1. Both existing tasks carry an acceptance suite passing pristine/gold/gold-prime and failing seeded, enforced as a seedcheck invariant; invariant 4 runs automated and green on both.
2. Both tasks carry their hack variants, admission green (`hacked-variants-green` executes its loop body on a real task for the first time).
3. Yield bar met with the frozen prompt: >=2 trusted candidates on at least 3 of the 4 clean real-task runs, and the real h5/h6 hack variants each produce >=1 `advtest_divergence`. This is the first real-repo H5/H6 detection measurement.
4. Evalkit emits the n=2 mini-table (detection lenient/strict, FP split gold vs gold-prime, attribution top-1/anywhere with posture named, per-category confusion, cost + latency) plus all three baseline rows, from snapshotted runs; the hand-computed confusion-matrix fixture test is green.
5. Suite green with zero API calls, ruff clean.

Wave B (= M5):

1. 12-task corpus admitted end to end: every task has seed, gold, gold-prime, its allocated hacks, an acceptance suite, and green invariants 4 and 5.
2. Eval A dev-set table published with baselines, FP split, attribution, cost actuals; weights tuned and frozen; bar checked and stated.
3. Eval B base arm complete: 24 attempts classified against acceptance suites; hack incidence and catch rate on GREEN-wrong reported; base-arm resolve rate and cost-per-resolve from the ledger.
4. `skeptic demo` runs keyless/dockerless/network-free in under 60 s and shows both banner colors with cited evidence.
5. README v1 leads with the eval table and pasted demo output; every number on it is measured; M6/M7 items are named as future, never faked.
6. Total M5 paid spend inside the $50 ceiling.

## Yield lane

Five levers, all honest, none touching a ladder rung:

1. Top-up retry: when `parse_candidates` returns fewer than `n_candidates` blocks, one follow-up call requests the shortfall (re-roll once on zero). Amends row 129's "exactly one generation call"; the retry prompt carries the same two inputs, so the boundedness contract is intact. Own DECISIONS row.
2. Parse filter: a fenced block only counts as a candidate if it contains a `def test_*` (AST walk). Kills the quoted-fragment class that wasted a slot and a reference run on click gold-prime.
3. Prompt calibration, reference rung: the prompt states that tests run against the true current implementation and any test failing there is discarded; assert only behavior traceable in the shown source; prefer derivable exact values over guessed edge behavior.
4. Prompt calibration, seeded_green rung: derive discriminating inputs from the problem statement's symptom (parametrize near the described failure regime; exact expected output over weak structural properties). The forbidden version, showing the seed patch or seeded-tree behavior, stays forbidden.
5. Wider pristine context: the caller may add one-hop pristine imports of the changed files to the `sources` dict (rich's 1,772-token prompt is the motivating case). The two-parameter signature survives; the pinned-contract DECISIONS row is amended to say "pristine source text, possibly more than the changed files". Capped at 30,000 input tokens per generation call (~$0.03 at the Haiku rate).

Observability first: raw testgen responses and stop_reason persist alongside the artifact (today the parsed candidate source survives in `t2_advtests.json`, but the raw response envelope and stop_reason do not, so why a call produced 1 block cannot be inspected; judge IO already persists both directions and is the precedent), and a cheap 5x repeat of the click prompt establishes the generation-count base rate before any lever is credited. Testgen stays on `claude-haiku-4-5`; a stronger testgen model is a recorded fallback, not scope.

The prompt freezes when the exit bar (wave A criterion 3) is met, before any Eval A dev-set measurement.

## Evalkit and run persistence

Evalkit is a pure reader, matching the plan's "report, evalkit, and ledger are pure functions of the stream":

- Inputs per (task, variant): labels from `tasks/*.yaml` (`VariantSpec.label`/`hack_category`), verdict + deterministically ordered evidence from `verdict.json`, cost and latency from `trace.jsonl` (`llm_call.usage.usd`, `stage_end.dur_ms`).
- The eval driver mints an eval-run id and snapshots each driven run's `verdict.json` + trace into `evals/v1/runs/<eval-run-id>/<task>/<variant>/`. Two sharp edges it owns, both measured: verify's `run_id` is deterministic per (task, variant) and `trace.jsonl` opens in append mode, so reruns share one id and one growing file; the driver rotates the trace before each driven run so a snapshot holds exactly one run's events. The workdir slot is not versioned (each verify rebuilds the collector's artifact dirs in place), so the snapshot happens immediately after each run. `evals/v1/manifest.json` is assembled from parts that already exist (`verifier_revision()`, COLLECTOR_VERSION, patch sha256s, image ids, model + prompt identities, seeds) via the so-far-uncalled `write_manifest`.
- INFRA_ERROR rows (verdict None) are a separate column, excluded from detection and FP denominators, count always printed.
- Cache-hit rows join cost/latency from the originating run's trace and are marked replayed.
- Baselines: always-SUSPECT needs labels only; suite-green-only reads `fix_verified` from `t1_outcomes.json`; cheap-LLM-judge-alone reads `t2_judge.json`, which exists for every dev-set variant because the dev set runs under `--profile paid`. No judge-only runner.
- Attribution figures name their posture in the same sentence, per row 76.

## Corpus template (what every task ships)

One yaml (schema v1 + `acceptance_suite` block) · seed, gold, gold-prime diffs · allocated hack diffs · `acceptance/<task-id>/` suite · admission report in `docs/admission/` · green invariants 1-5. Holdout of acceptance suites is mechanical, stated here so the claim is checkable: the Builder workspace is a `git archive` of the target repo, so acceptance files are physically absent from it, and `build_testgen_prompt`'s signature has no parameter through which they can arrive.

## Demo

`skeptic demo` copies the bundled minirepo to a temp dir, materializes the gold and h1-excision variants, runs pytest collection and the suite by subprocess (no Docker, no network, no keys), and renders two verdicts through the real aggregator: gold PASS (green), h1 FAIL (red, `t1_collect` hard row citing the vanished nodeid and artifact path), then `Cost: $0.00`. DX1 specifies a single FAIL verdict; the gold PASS runs in front of it as an extension, not a replacement. A `demo` profile excuses `t1_coverage`, `t2_mutation`, `t2_probe`, and both paid checks as NOT_APPLICABLE with the profile named (the wave B mechanism, third profile value), so the PASS satisfies mandatory completion honestly and nothing is faked. The verdict-rendering block in `verify` is extracted into one function both commands share.

## README v1

Above the fold: the one-sentence value prop (already in pyproject), the Eval A dev-set table (n, corpus version, run date) with its three baseline rows and the FP split, the demo command with pasted real output, cost actuals in the wave B style, and one plain line stating the holdout lands at M6 and the timed fresh-clone at M7. Second screen: the three lanes (free demo / deterministic verify / paid end-to-end) with measured runtime and max cost, architecture, tradeoffs, limits (including the thin-yield history and what fixed it), related work. No placeholder cells anywhere.

## Spend plan

Ceiling $50 (owner ruling, this brainstorm), per-command confirmations unchanged. Expected: yield iteration $1-3 (testgen calls measured $0.009-0.035; paid verifies measured $0.010-0.036 each, rising with wider context and top-up retries) · Eval A dev set ~53 paid verifies (12 gold + 12 gold-prime + 29 hacks), $1-3 measured, $5 with the tuned prompt's larger calls · Eval B 24 attempts, $2-13 at measured actuals ($0.06-0.53/attempt), worst $36 at plan pricing before prompt caching · invariant-4 gates free (deterministic). Expected total $5-20. Contingency order stays the plan's: base attempts 2 to 1 on already-resolved tasks first. Never thin per-task rigor.

## Error handling

- Acceptance runner: suite errors (collection failure, missing dir) are INFRA_ERROR, never a RED/GREEN classification; a suite that fails on pristine at authoring time is a corpus bug caught by invariant 5.
- Eval driver: a failed run records its INFRA_ERROR verdict and the sweep continues; the table's INFRA column carries the count. Corrupt trace lines skip+warn (existing `read_trace` contract); missing usage marks the row `estimated:true`.
- Demo: never touches Docker or the network by construction; a missing bundled fixture is a packaging bug and says so with the exact reinstall command.
- Testgen retry: one top-up, then the run proceeds with what it has; shortfall is a yield stat, never INFRA.
- Everything else inherits M4's contracts unchanged (INFRA_ERROR discipline, enrichment isolation, ladder rejections are never errors).

## Testing

- Acceptance machinery: invariant-5 fixture matrix on the minirepo (suite passes pristine/gold/gold-prime, fails seeded; INFRA on collection error); invariant-4 gate test with a stubbed verify.
- Yield levers: parse-filter rejection tests (quoted fragment, assertion-free block); top-up retry test with a faked short response; prompt-content pins updated for the widened sources dict (the "never receives test content" pin holds by construction and stays).
- Evalkit: the §11 hand-computed confusion-matrix fixture; snapshot layout round-trip; INFRA-column and replayed-row marking tests; baseline rows computed from committed fixture verdicts.
- Eval B mechanics: attempt-salt cache-key test (two attempts, two entries); acceptance classifier mapping tests (RED, GREEN-wrong, GREEN-correct, INFRA_ERROR).
- Demo: end-to-end in CI (it is keyless and dockerless by design, so CI runs the real thing); both verdicts asserted; NO_COLOR output stable.
- Zero API calls in the suite throughout; live runs stay owner-driven CLI invocations with confirmation, actuals to the ledger.

## Decisions made in this brainstorm

1. Corpus is 12 tasks on click + rich (6 per repo), no third-repo admission in M5; the pressure-arm subset becomes 3 per repo, a recorded amendment to plan §8's "2 per repo".
2. M5 runs as a tracer bullet: wave A proves acceptance machinery, hack variants, yield fix, and evalkit on the two existing tasks (real n=2 mini-table); wave B scales to 12 and runs the evals. Wave B's plan is authored at wave A exit.
3. The yield fix runs as a parallel lane inside wave A; the testgen prompt freezes at the measured bar before any Eval A measurement.
4. M5 paid-spend ceiling is $50.
5. `skeptic doctor` moves to M6, ratifying the README line that previously had no decision behind it: a recorded amendment to plan §14 (which put doctor in M5), like the §8 subset amendment. `runs list`/`report` go with it.
6. rich-0001's cosmetic gold-prime, shipped in M4 wave A with its weakness documented in the yaml, is ratified as corpus-final; the FP-split denominator stays whole at n=12. New-task admission screens for materially different fixes.
7. Both testgen contract amendments approved: top-up retry (relaxes "exactly one call") and wider pristine context (sources dict may carry one-hop pristine imports). Each gets a DECISIONS row; ladder rungs untouched.
8. Hack allocation is the mixed ~29-diff table (h5 x6, h6 x6, h7 x3, h8 x3, h1/h2/h3/h4/h9 x2 each, h10 x1), 2-3 per task, assigned by bug shape.
9. Demo is the two-verdict subprocess-pytest shape (gold PASS + h1 FAIL) with pytest promoted to a runtime dependency and a `demo` profile excusing execution-heavy and paid checks by name.
10. Eval B attempts get an attempt id in the build cache key and per-attempt build dirs; Builder prompt caching lands before the base arm.
11. The dev set runs entirely under `--profile paid`, so the judge-alone baseline needs no bespoke runner.
12. INFRA_ERROR rows sit in their own column, outside detection/FP denominators.
13. The reference-feedback repair round is deferred; the 2x rerun-before-flag stays deferred unless a flake bites during dev-set runs. Both recorded.
