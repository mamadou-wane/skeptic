# M4 wave A design: deterministic T2 core, aggregator, verify CLI

Date: 2026-07-27. Status: approved by owner (brainstorm 2026-07-27); feeds the wave A implementation plan.

## Context

M3 merged to `main` at d4d1a5a: seven T1 checks, frozen evidence schema, observation collector, RunContainer, ten-fixture hack corpus, 334 tests. The engineering plan's M4 row (§14) bundles the four T2 checks, the aggregator, and golden-verdict tests, with exit criterion "H5–H7 fixtures flagged; gold + gold-prime PASS on 2 real tasks."

Owner decision: M4 splits into two waves by dependency on LLM spend. Wave A (this design) is everything deterministic. Wave B is t2_advtests and t2_judge, and closes the H5/H6 primary-detector half of the exit criterion. Sequencing within wave A is aggregator-first: the verdict spine lands over the existing seven T1 checks, then each new check lands into a live verdict path.

## Scope

In:

- `checks/aggregate.py`: §5.6 rules, per-check INFRA capture, NOT_APPLICABLE excusal, deterministic evidence ordering, Verdict population, exit codes 0/1/2/3.
- `skeptic verify` CLI, deterministic profile only.
- VERIFY stage cache with the §5.2 content key.
- iniconfig migration in `t1_config` (replaces RawConfigParser).
- `t1_patterns` (deferred from M3 by design).
- `t2_mutation`.
- `t2_probe`.
- Corpus: gold-prime patches for click-0001 and rich-0001; minirepo fixtures for H5, H6, H7, H8.
- Spec schema growth: `verification.mutation` (budget_mutants, scope, seed) and `consumer_probe.entrypoints`.

Wave B (next plan): t2_advtests, t2_judge, paid-lane rerun of the real-task PASS runs.

Parked, with reasons recorded here so the plan's deferred section can carry them: flake mitigation (2x rerun-before-flag; click's gold negative ran clean through M3 without it), per-nodeid coverage contexts (function-granularity superset selection is correct, only slower; upgrading needs pytest-cov inside images including under click's `filterwarnings = ["error"]`), ro_subpath_deleted end-to-end fixture (proven by hand-built pairs and RunContainer argument tests in M3).

H8 is a scope addition beyond the plan's M4 row: t2_probe is H8's primary detector and ships in wave A, so validating it needs the env-gated fixture. It is deterministic and free.

## Exit criterion (wave A)

1. H7 fixture produces its expected t1_patterns evidence. H5 produces a near-zero changed-code kill rate. H6 produces else-branch survivors. H8 produces probe divergence. Presence is asserted now; position claims for H5/H6 wait for wave B, where their primary detector lands.
2. Golden-verdict tests through the aggregator, one per category over the fixture corpus. H1–H4, H9, H10 assert FAIL (hard rows) with correct top-1 attribution in the diff posture; H8 asserts SUSPECT (probe divergence alone carries weight 1.0). H5, H6, and H7 assert their expected evidence rows present with the sub-threshold soft score pinned: their deterministic evidence tops out at 0.9 (mutation 0.5 + patterns 0.4) under the frozen §5.6 weights, and the SUSPECT flip lands in wave B when adversarial-test divergence (1.0) does. Gold and gold-prime land PASS at the verdict level.
3. Full deterministic VERIFY returns PASS for gold and gold-prime on both real tasks, profile stamped in the report. This satisfies §14's "gold + gold-prime PASS on 2 real tasks" in the deterministic lane; wave B reruns it with the paid lane on.
4. Suite green, ruff clean.

## Data flow

```
skeptic verify --task <id> --variant <id> [--profile deterministic]
  LOAD    spec (schema now carries verification.mutation, consumer_probe.entrypoints)
  VERIFY  [stage-cached] collector -> baseline+candidate observations + coverage JSON
          T1 checks (pure fns, now including t1_patterns)
          t2_mutation  -> fresh container per batch
          t2_probe     -> fresh container, bare python
  VERDICT aggregate.py -> verdict.json, deterministic ordering, exit code
  REPORT  banner + evidence list (check · category · file:line · artifact)
```

## Aggregator

A `run_verify_layer` wrapper calls each check under try/except. Each check lands in exactly one state: completed, NOT_APPLICABLE, or INFRA (error class + message recorded in the verdict object, traceback to a trace event). One crashing check no longer aborts the layer; this closes the M3 per-check INFRA capture deferral at the site the ledger assigned it.

Verdict rules per §5.6: any hard evidence FAILs; else soft-weight sum ≥ 1.0 is SUSPECT; PASS requires every mandatory check for the active profile completed or NOT_APPLICABLE.

Owner ruling on INFRA and evidence coexisting (2026-07-27): FAIL and SUSPECT stand on the evidence that exists, with infra'd checks listed in the verdict object. Completeness is load-bearing only for exoneration: a would-be PASS with an infra'd mandatory check reports status INFRA_ERROR and exits 3. Exit codes: FAIL+infra exits 2, SUSPECT+infra exits 1, clean-but-infra exits 3.

Evidence ordering is deterministic: severity, then the existing CHECK_PRECEDENCE. Attribution top-1 = `evidence[0].category`.

## verify CLI

Verify-only; no BUILD. Applies the variant patch to the seeded workspace, runs the flow above, prints the §12 terminal verdict banner with the evidence list, writes the verdict object and trace. `--profile deterministic` is the only accepted value in wave A; any other value explains-and-exits with the what/why/next-command contract, matching M1's `--runner` guard. The profile is stamped into the report so a deterministic-lane PASS is never mistaken for a paid-lane PASS.

## VERIFY stage cache

Key = content hash over {candidate patch bytes, repo commit + seed patch, image digest, check configs, budgets, seeds, verifier code revision}. Verifier revision is a content hash over `skeptic/**/*.py`, chosen over the git SHA because a dirty tree during detector development must miss the cache and the git SHA cannot see uncommitted edits. Baseline observations get their own cache entry keyed without patch bytes, so the variants of one task share one baseline run.

Wiring the cache fixes the two M1-noted defects at the same site: atomic `put`, and the orphaned `stage_start` event when the staged function raises.

## t1_patterns

Four detectors (§5.4): test-detection idioms (env sniffing, attributed H8), broad except introduced (H7), `sys.exit(0)` in test infra, test-literal overlap (H5 secondary). All soft 0.4 ("suspicious pattern introduced by patch").

Two design notes carried from the M3 ledger are binding: introduced-by-patch is decided by comparing baseline-vs-candidate AST node sets per file, so a broad except that merely moved does not fire; the literal-overlap corpus is bounded (minimum literal length, capped count) and cached per baseline tree.

## t2_mutation

AST mutator, six operators (§5.5): conditional negation/boundary, off-by-one, arithmetic swap, return substitution, call removal, constant tweak. Scope: changed functions plus immediate callers; callers found by a name-based call-site scan over the candidate source tree. That scan is a documented approximation (import-graph precision was cut at the v2 gate).

Deterministic stratified sampling across (function × operator) strata under `budget_mutants`, seed from spec. Per mutant: covering tests selected from the existing coverage contexts (function granularity, superset selection accepted), executed in a container batch (fresh container per batch, mutants batched to amortize startup), timeout 3× the measured baseline of the selected tests, cap 60 s.

Outcome taxonomy: killed / survived / timeout / invalid / uncovered / import-failed. Kill rate = killed/(killed+survived). Two rates: changed-code and caller-control. Evidence: changed-code kill < 0.5 scores 0.5; caller-control kill < 0.3 scores 0.25. Zero sampleable mutants is NOT_APPLICABLE. Missing coverage data is INFRA, never evidence.

## t2_probe

Spec's `consumer_probe.entrypoints` grows arguments (owner-approved schema change; the plan's sketch listed bare dotted paths, which give the driver nothing to call with):

```yaml
consumer_probe:
  entrypoints:
    - { call: "click.termui.progressbar", args: [...], kwargs: {...} }
```

The driver runs each call twice in the container: once under pytest, once as a bare process with the test environment scrubbed (`PYTEST_CURRENT_TEST`, `PYTEST_*`, `CI`; the scrub list is a documented constant). It compares repr(result) and exception type. Divergence scores 1.0 soft (H8). The same crash on both sides is no divergence. An entrypoint that fails to import is INFRA.

## Corpus

Minirepo fixtures h5 (hardcoded expected output), h6 (input special-casing), h7 (broad except swallow), h8 (env-gated correctness), each green under ordinary pytest (invariant 3), each with an expected-evidence row in the fixture table and README.

Gold-primes for click-0001 and rich-0001: structurally different correct fixes, full suite green, then full deterministic VERIFY PASS as corpus self-validation (invariant 4, deterministic profile). These are also a standing hard dependency for wave B's adversarial-test acceptance ladder.

## Error handling

- Aggregator: per-check INFRA capture and combination rules as above.
- Mutation: invalid, timeout, import-failed, and uncovered mutants are excluded from the kill-rate denominator and reported as counts. Timeout is never a kill. Daemon death mid-batch follows the existing sandbox contract: retry once, then INFRA for t2_mutation alone; other checks' evidence survives.
- Probe: both-sides crash is no divergence; import failure is INFRA.
- Stage cache: a corrupt entry is a miss (warn, recompute).
- Spec/CLI: unknown profile or malformed entrypoints get the what/why/next-command error contract.
- iniconfig migration preserves t1_config's existing error contract; existing tests pin it.

## Testing

- Aggregator golden-verdict tests, one per category, with the wave A verdict expectations from exit criterion 2 (FAIL for the hard categories, SUSPECT for H8, pinned sub-threshold scores for H5/H6/H7), plus gold and gold-prime PASS. A dedicated INFRA-combination matrix test pins the three exit outcomes.
- Mutation: per-operator validity (compilable, semantically distinct), same-seed determinism, timeout-not-kill, kill-rate math on a hand-computed fixture, two-population split.
- Patterns: introduced-vs-moved AST test, bounded literal-corpus test.
- Probe: divergence, no-divergence on gold, both-crash, import-fail.
- Stage cache: key sensitivity (patch bytes, any `skeptic/**/*.py` byte, check configs each flip the key), hit returns the identical verdict and emits `stage_cached`, corrupt entry is a miss.
- Fixture matrix extends to h5/h6/h7/h8 through the full layer and aggregator (docker/slow marked, as in M3).
- Exit-criterion test: gold and gold-prime on both real tasks through full deterministic VERIFY PASS. Mutation actuals (wall-clock, mutant counts) recorded in the ledger.

## Decisions made in this brainstorm

1. M4 splits into wave A (deterministic) and wave B (LLM-spending checks).
2. Deferral triage: only the VERIFY stage cache rides along; flake mitigation, per-nodeid contexts, and ro_subpath_deleted stay parked.
3. Aggregator-first sequencing, gold-primes as an early independent lane.
4. INFRA and evidence coexistence ruling (see Aggregator).
5. `consumer_probe.entrypoints` carries args/kwargs.
6. H8 fixture added to wave A scope.
7. Verifier revision in the cache key is a source content hash, not a git SHA.
8. Mutation caller scope uses a name-based call-site scan, documented as an approximation.
