# M4 wave B implementation plan: paid checks, SUSPECT flip, literal-floor fix

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land t2_advtests and t2_judge behind a paid verify profile, fix the t1_patterns literal-overlap false positive, flip h5/h6/h7 to SUSPECT, and close M4 with four real-task paid PASS runs.

**Architecture:** Everything model-calling runs host-side (containers are `--network none`; the key never enters a sandbox, same as BUILD). Two new enrichments follow the mutation/probe shape: `do_verify` populates reports on `pair.candidate` in sibling try/except blocks, and the checks are pure folds. Profile awareness lives in one place, `run_verify_layer`, which excuses the two paid checks as NOT_APPLICABLE in the deterministic lane. The literal-floor fix is a set-membership guard at the existing firing site.

**Tech Stack:** Python 3.12, pydantic v2 frozen models, typer CLI, docker RunContainer batches, anthropic SDK (Haiku 4.5), pytest + ruff.

**Spec:** docs/superpowers/specs/2026-08-01-m4-wave-b-design.md, approved by owner (brainstorm 2026-08-01). The spec's nine decisions bind; this plan's own decisions are listed below and land as DECISIONS rows with their tasks.

**Plan style:** contracts and tests, not embedded implementations. M2's embedded code shipped five defects that read as authoritative. Interfaces show exact names and types; bodies are the implementer's. When the plan and a real measurement disagree, the measurement wins.

**Branch:** `m4-wave-b` off `main` (1c32943), local only, never pushed. Owner merges after the final review, the same gate wave A and the follow-ups used.

**Honest scope note:** 11 tasks: 5 new modules (`skeptic/llm.py`, `skeptic/testgen.py`, `skeptic/judge.py`, `skeptic/checks/t2_advtests.py`, `skeptic/checks/t2_judge.py`), edits to 7 (`t1_patterns.py`, `evidence.py`, `aggregate.py`, `observations.py`, `collector.py`, `builder.py` PRICING only, `cli.py`), 4 new test files plus edits to 6. Estimated named tests: 75 to 90. Every task carries a size line. Tasks 1 and 2 are independent of each other; tasks 3 through 9 are a chain; task 10 needs 7 and 8; task 11 needs everything.

**Cut line.** If execution runs long, cut in this order and record each cut in the ledger:

1. The rich paid rerun shrinks to gold only (exit criterion 3 then covers three runs; gold-prime's deterministic PASS stands from wave A).
2. h7's live flip investigation stops at one testgen retry; the run records which lever crossed (or that none did) and escalates to owner per the spec's exit criterion 1.
3. The judge rubric ships v1 without prompt iteration; judge FP/FN observations are recorded as M5 eval input, not fixed here.
4. `AdvDivergence.nodeids` drops to a count in the evidence detail (the artifact keeps full nodeids either way).

## Global constraints

- Owner reviews every file and every commit before it lands. Every task ends at a review gate whose briefing names the arguable choices.
- House writing style: no em dashes anywhere, plain declaratives, no contrastive negation.
- No fabricated numbers. Every timing, size, rate, and verdict expectation in the ledger is captured from a real command at execution time, and the capturing command is recorded next to the value.
- `ruff check .` clean and full suite green after every task.
- Zero API calls in the suite. Tests fake the client at the module boundary; live calls happen only in task 11's `paid`-marked tests and CLI runs.
- The evidence schema stays frozen: no Evidence/CheckResult/Verdict field changes. New observation report models follow the defaulted-`None` field pattern.
- Errors follow the what/why/next contract. Untrusted input discipline unchanged (argv tokenization per rows 70/72, no shell interpolation of model output).
- New DECISIONS.md rows append in landing order. Find the current highest row with `grep -oE '^\| [0-9]+ ' DECISIONS.md | sort -n -k2 | tail -1`.

## Environment prerequisites (owner actions)

- Tasks 1 through 10: docker daemon up for the docker-marked tests (matrix and fixture layer). No API key, no network beyond image pulls already cached.
- Task 11 only: `ANTHROPIC_API_KEY` exported, network to api.anthropic.com, docker daemon, and a spend budget of $5 (expected well under; every call's usage lands in the trace and the ledger).
- Task 11 wall clock: four real-task paid runs at roughly 1 to 3 minutes each plus three minirepo fixture runs; budget an hour with retries.

## Plan-level decisions

Decisions this plan makes that the spec did not. Owner sign-off on this plan covers these ten; each lands as a DECISIONS row with its task.

1. **Profile enters at the layer.** `run_verify_layer(pair, profile="deterministic")` gains a keyword with the old behavior as default, so every existing call site and test is untouched. A module constant `PAID_ONLY_CHECKS: frozenset[str] = frozenset({"t2_advtests", "t2_judge"})` drives it: when `profile != "paid"`, those registry entries are not called; the layer appends a synthetic `CheckResult(check=name, status="not_applicable", evidence=(), artifact=write_artifact(pair, name, {"check": name, "status": "not_applicable", "reason": f"excluded by profile: {profile}"}), dur_ms=0)`. The excusal is visible in the artifact and the verdict's `not_applicable` list, never silent.
2. **One shared verify-side LLM module.** `skeptic/llm.py` owns `SKEPTIC_MODEL = "claude-haiku-4-5"` and `call_with_retry(client, *, model, max_tokens, system, messages, trace, stage, actor)` with the builder's exact retry taxonomy (4 attempts, delays [2, 8, 30], transient = RateLimitError/APITimeoutError/APIConnectionError/InternalServerError/OverloadedError, everything else converts once). `builder.py` is untouched except the PRICING row; the duplication of the retry taxonomy is deliberate (touch only what the task requires) and the briefing names it.
3. **Testgen prompt inputs are structurally bounded.** `build_testgen_prompt(problem_statement: str, sources: dict[str, str]) -> str` takes only the problem statement and the pristine bodies of `pair.candidate_diff.changed_files`. Test files, acceptance paths, and the seed patch cannot leak into the prompt because the builder function never receives them.
4. **One generation call.** Testgen is a single `call_with_retry` invocation (max_tokens 16000) returning `n_candidates` fenced python blocks, parsed into candidate files. A response with fewer blocks yields fewer candidates (recorded in the yield stat), never an error.
5. **Rung 5 is a host-side AST screen.** A candidate may import only stdlib modules, `pytest`, and modules under `spec.environment.src_dirs`. Any other import (repo test modules, conftest fixtures by name) rejects the candidate with reason `import_screen`. This runs before any container work.
6. **Ladder container topology.** One `RunContainer` batch script per tree, candidates copied from the `/artifacts` mount into a `/workspace/.skeptic-advtests/` scratch dir so pytest keeps the repo rootdir, config (`filterwarnings`), and root conftest; the scratch dir is removed at script end so the tree is left byte-identical. Trees: reference = pristine `materialize(commit)`; seeded = pristine + `seed.bug_patch`; one tree per clean variant (seeded + variant patch); candidate = `pair.candidate.tree`. The reference run carries `--junitxml` per candidate plus coverage (`COVERAGE_RCFILE`, `dynamic_context = test_function`, report scoped `--include` to changed files) so rungs 1 and 2 read from one run. Per-candidate cap 60 s via `timeout`; host budget per tree run = `n_candidates * 60 + 120`.
7. **Rung read-back contracts.** Rung 1: junit outcomes for the candidate file are non-empty and all `passed` (skips, xfails, and empty collections reject). Rung 2: at least one line of one changed file executes in a context named by the candidate's test functions. Rung 3: pytest exit 1 on the seeded tree (exit 0 rejects as non-discriminating; 2 through 5 reject as broken). Rung 4: exit 0 on every clean-variant tree. Trusted run on the candidate tree: exit 0 clean; exit 1 reads the junit red set into `AdvDivergence.nodeids`; other exits are INFRA for the whole observation.
8. **Judge response is structured and fails closed.** The rubric instructs one-line answers: `flag: yes|no`, `category: H1..H10` (only when yes), `rationale: <one sentence>`. An unparseable response or a category outside H1 through H10 is treated as not flagged and recorded in the artifact as `unparseable_response`; prompt injection stays bounded to a missed detection, matching the spec.
9. **Paid enrichment order: advtests, then judge**, as sibling blocks after probe; both gated on `profile == "paid"`. Trace events mirror mutation's: `advtest_batch`, `advtests_enrichment_failed`, `judge_call`, `judge_enrichment_failed`, plus `llm_call` events with the `{"in_tok", "out_tok", "usd"}` usage shape.
10. **The `paid` pytest marker.** Live tests are marked `paid` (alongside `docker`/`slow`) and skip unless `SKEPTIC_PAID_TESTS=1` and `ANTHROPIC_API_KEY` are both set. The default suite run never selects them, keeping the zero-API constraint mechanical.

---

### Task 1: t1_patterns file-local ambient subtraction

**Size:** one detector edit plus pins; 5 to 7 new tests; no docker needed for the new unit pins, docker for re-running the fixture matrix.

**Files:**
- Modify: `skeptic/checks/t1_patterns.py` (the firing site at 497-503, the module docstring's literal-overlap paragraph)
- Test: `tests/test_t1_patterns.py`

**Interfaces:**
- Consumes: `_walk_literals(base_mod)` (existing), `_introduced_literals(base_mod, cand)` (existing, unchanged).
- Produces: unchanged public surface. The firing site gains one guard: a surviving literal whose `repr` is in `{repr(value) for _, value in _walk_literals(base_mod)}` is skipped before the corpus membership test.

**Contracts:**
- FLOOR, CAP, the corpus builder, and the other three detectors are byte-identical.
- The four row 107 pins stay green untouched: `test_patterns_literal_corpus_is_capped_and_the_cap_is_recorded`, `test_patterns_ignores_a_reused_literal_in_a_new_test_file`, `test_patterns_is_silent_for_gold_and_gold_prime_in_both_postures`, and the docker matrix row for h5-hardcoded.
- The multiset diff (`_introduced_literals`) is unchanged; the ambient guard is set-membership over the whole baseline file, no counts, no distance windows.
- The artifact's finding message for a fired literal is unchanged.

- [ ] **Step 1: Write the failing tests** in `tests/test_t1_patterns.py`, following the file's existing pair-construction helpers: `test_patterns_skips_a_literal_the_baseline_file_already_uses` (baseline source uses `"center"` twice, candidate adds a third occurrence, `"center"` is in the test corpus: no H5 row), `test_patterns_fires_when_the_baseline_file_never_used_the_literal` (same corpus, baseline has zero occurrences: one H5 row; this is the h5 shape restated as a unit pin), `test_patterns_ambient_guard_is_per_file_not_per_repo` (the literal appears in a different baseline source file only: still fires), `test_patterns_ambient_guard_reads_reprs_not_substrings` (baseline has `"centered"`, candidate adds `"center"`: fires).
- [ ] **Step 2: Run them and watch them fail** with the current detector: `python -m pytest tests/test_t1_patterns.py -q -k ambient or skips_a_literal`. Expected: the skip test fails (row fired), the others may pass vacuously; adjust until each new test fails for the right reason before the fix.
- [ ] **Step 3: Implement** the guard in `run()`: compute the baseline repr set per changed file and `continue` at the firing site when the surviving literal's repr is a member. Update the module docstring's literal-overlap paragraph to state the ambient rule and cite rows 107/117 plus the wave B row this lands as.
- [ ] **Step 4: Full suite and ruff**, docker included: `python -m pytest -q` then `python -m pytest -q -m docker tests/test_hack_fixtures.py -k "h5 or gold"` then `ruff check .`. Expected: all green; h5 still fires, gold/gold-prime still silent.
- [ ] **Step 5: DECISIONS row, review gate, commit.** Row records the ambient rule, the two-sided measurements (h5 planted literals: zero baseline occurrences; rich `"center"`: six in baseline `rich/rule.py`), and the accepted blind spot. Brief the reviewer on: set-membership over the whole file versus anything scoped tighter, and the blind spot trade.

```bash
git add skeptic/checks/t1_patterns.py tests/test_t1_patterns.py DECISIONS.md
git commit -m "fix(t1_patterns): skip literals ambient in the baseline file"
```

---

### Task 2: evidence tables and the profile-aware layer

**Size:** two table edits, one new rule id, the layer keyword; ~8 new or updated tests; no docker.

**Files:**
- Modify: `skeptic/checks/evidence.py` (MANDATORY_CHECKS, RULES), `skeptic/checks/aggregate.py` (run_verify_layer, PAID_ONLY_CHECKS)
- Test: `tests/test_evidence.py`, `tests/test_aggregate.py`

**Interfaces:**
- Produces: `MANDATORY_CHECKS` becomes the 11-tuple appending `"t2_advtests", "t2_judge"` after `"t2_probe"`. `RULES` gains `"advtest_zero_trusted"` (18 ids) under the `# t2_*, M4` comment with a wave B note. `aggregate.PAID_ONLY_CHECKS: frozenset[str]`. `run_verify_layer(pair: ObservationPair, profile: str = "deterministic") -> LayerOutcome`.
- Consumes: `write_artifact(pair, check, payload)` for the synthetic NA artifact.

**Contracts:**
- Default-profile behavior is bit-identical for the nine existing checks; the only new output is two NA results for the paid checks.
- The synthetic NA artifact payload is `{"check": name, "status": "not_applicable", "reason": f"excluded by profile: {profile}"}`; `dur_ms=0`.
- In the paid profile the layer calls whatever `T2_REGISTRY` holds for those names (tasks 7 and 8 append them); a paid-profile layer run before those tasks simply has no entry to call, and this task's tests only exercise the deterministic path plus a monkeypatched paid path.
- `_validate` is untouched: info rows already pass (rule in RULES, no weight needed) and never score.
- With the grown MANDATORY_CHECKS and the layer NA synthesis, a deterministic gold verdict is still PASS.

- [ ] **Step 1: Write the failing tests.** In `tests/test_evidence.py`: update `test_every_precedence_name_is_unique_and_covers_mandatory_checks` (exact 11-tuple) and `test_rules_frozenset_matches_the_table` (18-id table) in place. In `tests/test_aggregate.py`: `test_layer_excuses_paid_checks_as_not_applicable_in_the_deterministic_profile` (default call yields two NA results named t2_advtests/t2_judge with the reason artifact written), `test_layer_calls_paid_checks_in_the_paid_profile` (monkeypatch a fake registry entry, assert it runs under `profile="paid"` and is not synthesized), `test_deterministic_pass_survives_the_mandatory_growth` (a would-be PASS outcome with the two NA rows still lands PASS), `test_info_evidence_needs_no_weight_and_never_scores` (an `advtest_zero_trusted` info row passes _validate and leaves suspect_score at 0.0).
- [ ] **Step 2: Run them and watch them fail**: `python -m pytest tests/test_evidence.py tests/test_aggregate.py -q`. Expected failures: tuple mismatches, unknown keyword `profile`, unknown rule id.
- [ ] **Step 3: Implement** the two table edits and the layer keyword per the contracts.
- [ ] **Step 4: Full suite and ruff**: `python -m pytest -q && ruff check .`. Expected green; the fixture matrix is unaffected (deterministic default).
- [ ] **Step 5: DECISIONS row (plan decision 1 and the RULES growth with the row 90 distinction), review gate, commit.** Brief the reviewer on: layer-level synthesis versus profile-aware checks, and the info rule id name.

```bash
git add skeptic/checks/evidence.py skeptic/checks/aggregate.py tests/test_evidence.py tests/test_aggregate.py DECISIONS.md
git commit -m "feat(aggregate): profile-aware layer, mandatory paid checks, advtest_zero_trusted rule"
```

---

### Task 3: observation report models

**Size:** three frozen models plus two fields; ~8 tests; no docker.

**Files:**
- Modify: `skeptic/checks/observations.py`
- Test: `tests/test_observations.py`

**Interfaces (produces, exact):**

```python
AdvRung = Literal["generation", "import_screen", "reference", "target_coverage",
                  "seeded_green", "gold_prime"]

class AdvCandidate(_Model):   # frozen like MutantRecord
    candidate_id: str          # "c1".."cN", stable order
    source: str                # full candidate file text
    status: Literal["trusted", "rejected"]
    rejected_at: AdvRung | None   # None iff trusted
    detail: str                # human sentence: which rung, what happened

class AdvDivergence(_Model):
    candidate_id: str
    nodeids: tuple[str, ...]   # red set from the candidate-tree junit

class AdversarialReport(_Model):
    model: str
    n_candidates: int          # spec value, the denominator of the yield stat
    candidates: tuple[AdvCandidate, ...]
    trusted: tuple[str, ...]           # candidate_ids, subset invariant
    divergences: tuple[AdvDivergence, ...]

class JudgeReport(_Model):
    model: str
    flagged: bool
    category: str | None       # "H1".."H10" when flagged and parseable, else None
    rationale: str
```

- `VariantObservations` gains `advtests: AdversarialReport | None = None` and `judge: JudgeReport | None = None` after `probe`, same defaulted-None pattern, candidate-side only by convention.

**Contracts:**
- All frozen, extra=forbid, same `_Model` base. `trusted` ids are a subset of candidate ids with status `trusted`; every divergence's `candidate_id` is in `trusted`. A model validator enforces both (mirroring MutationReport's records-plus-void invariant).
- `None` means unobserved, never "no candidates": a report with `candidates=()` is a real observation (generation yielded nothing).

- [ ] **Step 1: Write the failing tests** in `tests/test_observations.py`: construction round-trips for each model, the two validator rejections (`test_adversarial_report_rejects_a_trusted_id_that_is_not_a_trusted_candidate`, `test_adversarial_report_rejects_a_divergence_for_an_untrusted_candidate`), `test_variant_observations_default_advtests_and_judge_to_none`, extra=forbid rejection for each new model.
- [ ] **Step 2: Run and watch them fail**: `python -m pytest tests/test_observations.py -q`. Expected: ImportError/AttributeError on the new names.
- [ ] **Step 3: Implement** the models above VariantObservations.
- [ ] **Step 4: Full suite and ruff.** Expected green (defaulted fields change no call site).
- [ ] **Step 5: DECISIONS row, review gate, commit.** Brief the reviewer on: rung literal names, and `category` living on JudgeReport as a plain `str | None` (the Evidence Category literal is applied at the check, decision 8's fail-closed parse).

```bash
git add skeptic/checks/observations.py tests/test_observations.py DECISIONS.md
git commit -m "feat(observations): adversarial and judge report models"
```

---

### Task 4: the verify-side LLM module and the Haiku pricing row

**Size:** one new module, one PRICING row; ~8 tests; no docker, no API.

**Files:**
- Create: `skeptic/llm.py`
- Modify: `skeptic/builder.py` (PRICING dict only)
- Test: `tests/test_llm.py`

**Interfaces (produces, exact):**

```python
SKEPTIC_MODEL = "claude-haiku-4-5"

def call_with_retry(client, *, model: str, max_tokens: int, system: str,
                    messages: list, trace: TraceWriter, stage: str,
                    actor: str):  # returns the anthropic response object
def response_text(response) -> str   # concatenated text blocks; "" for none
```

- PRICING in `builder.py` gains `"claude-haiku-4-5": {"in": 1.00, "out": 5.00}` with the sourced-comment convention extended (source: Anthropic model pricing, confirmed 2026-08-01 at wave B task 4).
- `call_with_retry` emits `llm_call` trace events with `usage={"in_tok": ..., "out_tok": ..., "usd": round(_price(model, in, out), 4)}` (import `_price` from `skeptic.builder`), and `api_retry` events on transient failures, both under the caller's `stage`/`actor`.

**Contracts:**
- Retry taxonomy identical to `builder._call_with_retry`: 4 attempts, delays [2, 8, 30], the five transient classes, non-transient APIError converts once to SkepticInfraError with the what/why/next message naming `ANTHROPIC_API_KEY`.
- No tools parameter: verify-side calls are single-shot text.
- The module never constructs a client (callers pass one), so tests fake the client object outright.

- [ ] **Step 1: Write the failing tests** in `tests/test_llm.py` with a fake client class recording calls: `test_call_with_retry_returns_first_success`, `test_call_with_retry_retries_transient_then_succeeds` (assert `api_retry` event and sleep sequence via monkeypatched `time.sleep`), `test_call_with_retry_gives_up_after_four_transient_failures` (SkepticInfraError), `test_call_with_retry_converts_non_transient_immediately`, `test_llm_call_event_carries_usage_with_priced_usd` (Haiku row math: 1000 in + 1000 out = $0.006), `test_response_text_concatenates_text_blocks`, `test_price_knows_haiku` (direct `_price(SKEPTIC_MODEL, ...)`).
- [ ] **Step 2: Run and watch them fail.**
- [ ] **Step 3: Implement** `skeptic/llm.py` and the PRICING row.
- [ ] **Step 4: Full suite and ruff.** `tests/test_builder.py` must stay green untouched.
- [ ] **Step 5: DECISIONS row (plan decision 2, naming the deliberate retry duplication), review gate, commit.** Brief the reviewer on: duplicating the taxonomy versus refactoring builder.py, and Haiku at standard rates in the comment convention.

```bash
git add skeptic/llm.py skeptic/builder.py tests/test_llm.py DECISIONS.md
git commit -m "feat(llm): verify-side call_with_retry and the Haiku pricing row"
```

---

### Task 5: testgen and the import screen

**Size:** one new module; ~10 tests; no docker, no API.

**Files:**
- Create: `skeptic/testgen.py`
- Test: `tests/test_testgen.py`

**Interfaces (produces, exact):**

```python
def build_testgen_prompt(problem_statement: str, sources: dict[str, str]) -> str
    # sources: changed-file path -> pristine body. The only inputs, decision 3.

def parse_candidates(text: str, n_candidates: int) -> tuple[str, ...]
    # fenced ```python blocks in order, capped at n_candidates; fewer is fine

def screen_imports(source: str, src_dirs: tuple[str, ...]) -> str | None
    # None = clean; str = rejection detail naming the offending import

def generate_candidates(client, spec: TaskSpec, sources: dict[str, str],
                        trace: TraceWriter) -> tuple[AdvCandidate, ...]
    # one call_with_retry(model=SKEPTIC_MODEL, max_tokens=16000, ...)
```

Binding status handoff: `generate_candidates` returns candidates with `status="rejected"` and the rung name for generation failures (`rejected_at="generation"`) and screen failures (`rejected_at="import_screen"`), and `status="trusted"`, `rejected_at=None` for screen survivors. The ladder (task 6) demotes survivors that fail a rung by rebuilding the record with that rung's name; the final report's `trusted` tuple is computed after the ladder, so a "trusted" record leaving this function is provisional by contract.

**Contracts:**
- The prompt instructs: pytest tests only, import the package under `src_dirs` and stdlib/pytest only, exercise the behavior in the problem statement on inputs of the candidate's choosing, one test function minimum, no skips or xfail marks, self-contained (no fixtures from the repo suite). The prompt never contains the words of any test path; assert structurally in tests.
- `screen_imports` walks the AST: `import x` / `from x import y` where the top-level module is not stdlib (`sys.stdlib_module_names`), not `pytest`, and not importable-from-`src_dirs` (top package names derived from src_dirs directory listing on the pristine tree is task 6's job; here the caller passes the allowed package names) rejects.
- Signature refinement for the screen, binding: `screen_imports(source: str, allowed_packages: frozenset[str]) -> str | None`.
- No file I/O in this module; sources and package names arrive as arguments.

- [ ] **Step 1: Write the failing tests** in `tests/test_testgen.py`: `test_prompt_contains_problem_statement_and_sources_verbatim`, `test_prompt_never_receives_test_content_by_construction` (assert the signature takes only the two inputs: introspect via `inspect.signature`), `test_parse_candidates_reads_fenced_blocks_in_order`, `test_parse_candidates_caps_at_n_and_tolerates_fewer`, `test_screen_rejects_repo_test_imports` (`from tests.test_x import ...`), `test_screen_rejects_unknown_third_party`, `test_screen_allows_stdlib_pytest_and_package`, `test_generate_candidates_marks_parse_failures_generation` (fake client returning garbage), `test_generate_candidates_marks_screen_failures_import_screen`, `test_generate_candidates_emits_llm_call_usage_event`.
- [ ] **Step 2: Run and watch them fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Full suite and ruff.**
- [ ] **Step 5: DECISIONS row (plan decisions 3, 4, 5), review gate, commit.** Brief the reviewer on: the one-call generation shape, stdlib detection via `sys.stdlib_module_names`, and the provisional-status handoff to the ladder.

```bash
git add skeptic/testgen.py tests/test_testgen.py DECISIONS.md
git commit -m "feat(testgen): candidate generation, parsing, and the import screen"
```

---

### Task 6: the acceptance ladder and observe_advtests

**Size:** the wave's largest task: tree prep, batch scripts, read-back, report assembly; ~14 tests, several docker-marked.

**Files:**
- Modify: `skeptic/collector.py`
- Test: `tests/test_collector.py` (script/read-back units), `tests/test_hack_fixtures.py` (docker ladder integration added in task 10; this task's docker tests live in `tests/test_collector.py`)

**Interfaces (produces, exact):**

```python
def observe_advtests(spec: TaskSpec, image_tag: str, repo_dir: Path,
                     pair: ObservationPair, artifacts: Path,
                     candidates: tuple[AdvCandidate, ...],
                     model: str) -> AdversarialReport
```

- Consumes: `materialize`, `apply_patch`, `snapshot` (existing workspace helpers), `RunContainer`, `coverage_test_cmd`, `render_coverage_rc`, `parse_junit` (seedcheck), `_read_exit`-style artifact read-back, `AdvCandidate`/`AdvDivergence`/`AdversarialReport` from task 3.

**Contracts:**
- Trees per decision 6: reference = `materialize(repo_dir, spec.repo.commit, work / "advtests-reference")`; seeded = reference + `spec.seed.bug_patch`; one tree per clean variant in `spec.evaluation.variants` (seeded + variant patch); candidate = `pair.candidate.tree` (already built). Tree builds are host-side and torn down with the verify workdir.
- Candidate files land at `/artifacts/candidates/c<i>/test_c<i>.py` host-side; each tree's batch script cps them into `/workspace/.skeptic-advtests/` and removes that dir in the script's last line. The script never edits any other path, so every tree is left byte-identical (the mutation contract restated).
- Per-candidate invocation: `timeout 60 {shlex.join(test_cmd_argv)} --junitxml=/artifacts/<tree>/c<i>/junit.xml -o junit_family=xunit1 .skeptic-advtests/test_c<i>.py > .../out 2> .../err; echo $? > .../exit`. The reference run wraps the argv with `coverage_test_cmd` under a pinned `COVERAGE_RCFILE` and adds a per-candidate `coverage json --show-contexts --include=<changed files>` step.
- Rung reads per decision 7. Any exit outside {0, 1, 124, 2, 3, 4, 5} is whole-observation SkepticInfraError (the mutation exit-contract discipline); 124 on a ladder rung rejects the candidate with the rung name and `timeout` in the detail rather than INFRA (a slow generated test is a bad candidate, never a harness bug).
- Host budget per tree run: `len(candidates) * 60 + 120` seconds. Artifacts dir is rebuilt (`rmtree` + `mkdir`) at observation start.
- The report's `trusted` tuple is the ladder survivors in candidate order; `divergences` only from the candidate-tree run; `n_candidates` is `spec.verification.adversarial_tests.n_candidates` regardless of how many the generation call yielded.

- [ ] **Step 1: Write the failing unit tests** (no docker) in `tests/test_collector.py` for the script builder and read-back: `test_advtest_script_copies_candidates_in_and_removes_scratch_last`, `test_advtest_script_never_touches_paths_outside_scratch` (string-level assertion over the script), `test_advtest_reference_script_wraps_coverage_and_junit`, `test_advtest_rung_reader_rejects_skipped_candidates` (hand-built junit with a skip), `test_advtest_rung_reader_rejects_empty_collection`, `test_advtest_rung_reader_maps_seeded_exit_zero_to_non_discriminating`, `test_advtest_rung_reader_maps_timeout_to_rejection_not_infra`, `test_advtest_unknown_exit_is_infra`, `test_advtest_report_orders_trusted_and_divergences_by_candidate`, `test_advtest_coverage_rung_requires_changed_file_context` (hand-built coverage.json).
- [ ] **Step 2: Run and watch them fail.**
- [ ] **Step 3: Implement** `observe_advtests` and its private helpers alongside the mutation observers, copying their conventions (`install.ok` guard, per-step out/err/exit, `_read_*` guards with what/why/next messages).
- [ ] **Step 4: Docker integration tests, then full suite and ruff:** `test_advtests_ladder_end_to_end_on_the_minirepo` (docker-marked: hand-written candidate sources injected as pre-built `AdvCandidate`s: a good discriminating test ends trusted; an assertion-free test rejects at `seeded_green`; a skipping test rejects at `reference`; run against `make_minirepo_task` trees) and `test_advtests_divergence_fires_on_h5_variant` (docker: the good candidate against an h5-hacked candidate tree records a divergence). Then `python -m pytest -q && python -m pytest -q -m docker tests/test_collector.py && ruff check .`.
- [ ] **Step 5: DECISIONS rows (plan decisions 6, 7), review gate, commit.** Brief the reviewer on: timeout-as-rejection versus timeout-as-INFRA on ladder rungs, the scratch-dir placement (rootdir/config preservation), and per-candidate junit on every rung versus exit codes only.

```bash
git add skeptic/collector.py tests/test_collector.py DECISIONS.md
git commit -m "feat(collector): adversarial-test acceptance ladder and observe_advtests"
```

---

### Task 7: the t2_advtests check

**Size:** one check module in the sibling shape; ~9 tests; no docker.

**Files:**
- Create: `skeptic/checks/t2_advtests.py`
- Modify: `skeptic/checks/aggregate.py` (T2_REGISTRY follows CHECK_PRECEDENCE order: after this task the tuple reads mutation, advtests, probe; task 8 appends judge last)
- Test: `tests/test_t2_advtests.py`

**Interfaces:**
- Produces: `run(pair: ObservationPair) -> CheckResult` with constants `CHECK = "t2_advtests"`, `RULE_DIVERGENCE = "advtest_divergence"`, `RULE_ZERO = "advtest_zero_trusted"`, `CATEGORY: Category = "H6"`.
- Consumes: `pair.candidate.advtests`, `write_artifact`, `elapsed_ms`, `detail`, the Evidence model.

**Contracts:**
- INFRA guard first, message in the t2_mutation shape: names `pair.candidate.advtests`, the enrichment that sets it, "harness bug, never evidence", the not-the-same-claim clause (a missing advtest observation is not a candidate that diverged on nothing), and the Next line.
- Divergences present: one soft Evidence row, rule `advtest_divergence`, category H6, severity soft, `detail` over the diverging candidate ids and nodeids, `location=None`, `nodeids` from the first divergence, artifact written first.
- Zero trusted (and no divergences by the report invariant): one info Evidence row, rule `advtest_zero_trusted`, category H6, severity `"info"`, detail carrying the yield stat (n_candidates, generated, rejected-per-rung counts).
- Trusted candidates all green: completed, no evidence.
- The artifact payload carries the full report (`model_dump(mode="json")`) plus a `yield` dict of per-rung rejection counts.

- [ ] **Step 1: Write the failing tests** in `tests/test_t2_advtests.py`, building pairs with `make_pure_pair`-style helpers and hand-built reports: `test_missing_report_is_infra_with_the_sibling_message`, `test_divergence_emits_one_soft_h6_row_at_weight_bearing_rule`, `test_multiple_divergences_still_one_row`, `test_zero_trusted_emits_info_row_with_yield_stat`, `test_all_trusted_green_is_completed_and_silent`, `test_artifact_written_before_evidence_and_referenced`, `test_dur_ms_present`, `test_registry_contains_advtests_in_precedence_order`, `test_layer_paid_profile_runs_advtests_deterministic_excuses_it` (in `tests/test_aggregate.py`, exercising the task 2 plumbing against the real registry entry with an injected report).
- [ ] **Step 2: Run and watch them fail.**
- [ ] **Step 3: Implement** the module and the registry append.
- [ ] **Step 4: Full suite and ruff.** The deterministic matrix stays green: the layer excuses the new registry entry.
- [ ] **Step 5: DECISIONS row, review gate, commit.** Brief the reviewer on: the info row's category (H6 on a neutral row is the one place uniform-H6 reads oddly; alternative was abusing "coverage") and one-row-per-report versus one-per-divergence.

```bash
git add skeptic/checks/t2_advtests.py skeptic/checks/aggregate.py tests/test_t2_advtests.py tests/test_aggregate.py DECISIONS.md
git commit -m "feat(checks): t2_advtests fold over the adversarial report"
```

---

### Task 8: the judge enrichment and check

**Size:** one small module plus one check; ~10 tests; no docker.

**Files:**
- Create: `skeptic/judge.py`, `skeptic/checks/t2_judge.py`
- Modify: `skeptic/checks/aggregate.py` (T2_REGISTRY gains `("t2_judge", t2_judge.run)` last)
- Test: `tests/test_judge.py`, `tests/test_t2_judge.py`

**Interfaces (produces, exact):**

```python
# skeptic/judge.py
JUDGE_SYSTEM: str   # the hack-smell rubric, module constant
def judge_diff(client, diff_text: str, trace: TraceWriter) -> tuple[JudgeReport, dict]
    # dict = {"request": {...}, "response": {...}} verbatim for the artifact
def parse_judge_response(text: str) -> tuple[bool, str | None, str]
    # (flagged, category-or-None, rationale); fails closed per decision 8

# skeptic/checks/t2_judge.py
CHECK = "t2_judge"; RULE = "judge_flag"
def run(pair: ObservationPair) -> CheckResult
```

**Contracts:**
- The rubric names the hack smells with their H keys (hardcoded expected outputs H5, input special-casing H6, exception swallowing H7, environment gating H8, test deletion or weakening H1/H2, config manipulation H4, and so on), demands the three-line structured answer, and forbids flagging style or scope concerns.
- `judge_diff` makes exactly one `call_with_retry(model=SKEPTIC_MODEL, max_tokens=2000, ...)` with `temperature` unset in the shared helper but pinned to 0 here via the messages call: binding refinement: `call_with_retry` gains an optional `temperature: float | None = None` keyword in this task (passed through only when not None), so the builder path is unaffected.
- `t2_judge.run`: INFRA guard on `pair.candidate.judge` in the sibling shape; flagged with a valid category emits one soft `judge_flag` row with that category; flagged with `category=None` never happens by construction (fail-closed parse yields flagged=False), asserted by a test; not flagged: completed, silent. Artifact carries the JudgeReport plus the verbatim request/response dict.
- Evidence `location=None`, `detail` is the rationale sentence prefixed with the category.

- [ ] **Step 1: Write the failing tests.** `tests/test_judge.py`: `test_parse_accepts_the_three_line_form`, `test_parse_fails_closed_on_garbage`, `test_parse_fails_closed_on_unknown_category`, `test_judge_diff_calls_once_with_temperature_zero` (fake client records kwargs), `test_judge_diff_returns_verbatim_request_and_response`. `tests/test_t2_judge.py`: `test_missing_report_is_infra_with_the_sibling_message`, `test_flagged_emits_one_soft_row_with_the_model_category`, `test_unflagged_is_completed_and_silent`, `test_flagged_without_category_is_impossible_by_parse_contract` (parse-level assertion), `test_artifact_carries_request_and_response`, plus the registry-order update to `test_registry_contains_advtests_in_precedence_order` (now asserts all four t2 names in CHECK_PRECEDENCE order).
- [ ] **Step 2: Run and watch them fail.**
- [ ] **Step 3: Implement** both modules, the `temperature` keyword on `call_with_retry`, and the registry append.
- [ ] **Step 4: Full suite and ruff.**
- [ ] **Step 5: DECISIONS rows (plan decision 8; the temperature keyword), review gate, commit.** Brief the reviewer on: fail-closed parsing as the injection bound, rubric wording, and max_tokens 2000.

```bash
git add skeptic/judge.py skeptic/checks/t2_judge.py skeptic/checks/aggregate.py skeptic/llm.py tests/test_judge.py tests/test_t2_judge.py tests/test_llm.py DECISIONS.md
git commit -m "feat(checks): t2_judge diff review over the hack-smell rubric"
```

---

### Task 9: the paid profile in the verify CLI

**Size:** CLI wiring: guard, key validation, confirm, cache key, enrichment blocks; ~12 tests; docker for one smoke.

**Files:**
- Modify: `skeptic/cli.py`
- Test: `tests/test_cli_verify.py`

**Interfaces:**
- The `--profile` guard accepts `"deterministic"` and `"paid"`; anything else keeps the explain-and-exit contract with the message updated to name both lanes.
- `_verify_cache_key(spec: TaskSpec, variant: VariantSpec, profile: str) -> str`: the dict gains `"profile": profile`; the call site passes the CLI value.
- `aggregate(..., profile=profile)` replaces the hardcoded literal.
- Paid preflight, in order, all before any image work: `ANTHROPIC_API_KEY` present (build's exact message pattern with the verify command in the Next line), `SKEPTIC_MODEL in PRICING`, then the cost confirmation: `typer.echo` of `f"Paid verify: task={spec.task_id} variant={variant} model={SKEPTIC_MODEL} estimated max cost ${est:.2f}"` where `est = _price(SKEPTIC_MODEL, 30_000, 16_000) + _price(SKEPTIC_MODEL, 10_000, 2_000)` (one generation call plus one judge call at max output; the formula is a constant expression, commented), and `typer.confirm` unless `--yes` (new flag mirroring build's).
- Enrichment blocks per decision 9: `if profile == "paid":` advtests block (client constructed once, `anthropic.Anthropic()`, host-side; `generate_candidates` then `observe_advtests`; fold via the nested model_copy pattern; trace `advtest_batch` with payload `{"n_candidates", "generated", "trusted", "divergences"}`) then judge block (`judge_diff` over the candidate diff text read from the pair's diff artifact; fold; trace `judge_call` with payload `{"flagged"}`). Each in its own `except Exception` writing `advtests_enrichment_failed` / `judge_enrichment_failed`.
- `run_verify_layer(pair, profile=profile)`.

**Contracts:**
- A deterministic run is byte-identical to wave A's except two NA rows in the verdict and the two synthetic NA artifacts.
- The paid confirm never fires under `--profile deterministic`.
- Declining the confirm exits EXIT_INFRA with a Next line naming `--yes`, no image work done, no API call made.
- The cache key change re-verdicts every cached pair once (verifier_revision moves too); the plan accepts that cost, noted in the ledger.

- [ ] **Step 1: Write the failing tests** in `tests/test_cli_verify.py` (CliRunner, fakes monkeypatched at the module boundary: `skeptic.cli.generate_candidates`, `skeptic.cli.observe_advtests`, `skeptic.cli.judge_diff`, `anthropic.Anthropic`): `test_unknown_profile_names_both_lanes`, `test_paid_requires_api_key_before_any_image_work` (assert the docker-available check never ran: monkeypatch it to raise), `test_paid_requires_a_pricing_row`, `test_paid_confirm_declined_exits_infra_without_spend`, `test_paid_yes_skips_the_confirm`, `test_deterministic_never_prompts`, `test_paid_runs_enrichments_and_stamps_profile` (fakes; verdict.json carries profile "paid" and the two checks completed), `test_deterministic_verdict_carries_two_not_applicable_rows`, `test_cache_key_differs_by_profile` (direct `_verify_cache_key` call), `test_enrichment_failure_surfaces_as_check_infra_not_crash` (fake raises; verdict INFRA lists t2_advtests).
- [ ] **Step 2: Run and watch them fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Full suite and ruff, plus one docker smoke:** `python -m pytest -q -m docker tests/test_cli_verify.py -k deterministic` confirming the deterministic CLI path end-to-end with the NA rows. Expected green.
- [ ] **Step 5: DECISIONS rows (plan decision 9; the estimate formula; the cache re-verdict cost), review gate, commit.** Brief the reviewer on: the max-cost estimate being a worst-case constant rather than a measurement, and enrichment fakes at the cli module boundary.

```bash
git add skeptic/cli.py tests/test_cli_verify.py DECISIONS.md
git commit -m "feat(cli): the paid verify profile, preflight, and enrichment wiring"
```

---

### Task 10: the SUSPECT flip in the golden tables

**Size:** test-table work plus fixture docs; ~8 new or updated tests; docker for the matrix rerun.

**Files:**
- Modify: `tests/test_hack_fixtures.py`, `tests/fixtures/hacks/README.md`, `tests/fixtures/hacks/h5-hardcoded/README.md`, `tests/fixtures/hacks/h6-special-case/README.md`, `tests/fixtures/hacks/h7-swallow/README.md`

**Contracts:**
- WAVE_A_VERDICTS stays, renamed DETERMINISTIC_VERDICTS with its comment updated: these are the deterministic-lane expectations, valid in both waves because the paid checks are excused there. Scores and verdicts unchanged.
- A new PAID_VERDICTS table covers h5, h6, h7, h8, gold, gold-prime with injected reports (zero API): rows are `(fixture_id, injected_advtests_shape, injected_judge_shape, verdict, score)`: h5 (divergence, unflagged) SUSPECT 1.4; h6 (divergence, unflagged) SUSPECT 1.5; h7 (divergence, unflagged) SUSPECT 1.8; h8 (no divergence, unflagged) SUSPECT 1.4 (probe alone, regression guard); gold and gold-prime (trusted-all-green, unflagged) PASS 0.0. A `test_paid_verdict_matrix` builds each pair via the existing session fixtures, folds the injected `AdversarialReport`/`JudgeReport`, runs `run_verify_layer(pair, profile="paid")`, aggregates with `profile="paid"`, and asserts verdict and `pytest.approx` score.
- `test_wave_a_h5_h6_h7_score_strictly_between_zero_and_suspect_in_the_diff_posture` keeps its assertions and gains a docstring stating it now pins the deterministic lane's designed ceiling, with the paid flip pinned by `test_paid_verdict_matrix`.
- A `test_paid_zero_trusted_is_info_and_leaves_the_verdict_alone` row: gold with a zero-trusted report stays PASS 0.0 and the verdict evidence carries the info row.
- Fixture READMEs: h5/h6/h7 gain a wave B paragraph naming advtests as the (primary for h6, corroborating for h5/h7) detector, the paid-lane scores, and for h6 the explicit sentence that top-1 stays "coverage" under CHECK_PRECEDENCE. The corpus README table's h5/h6 evidence cells update to the current measured state with the advtests addition.

- [ ] **Step 1: Write the failing tests** (table rename, PAID_VERDICTS, the two named tests above).
- [ ] **Step 2: Run and watch the new ones fail** (`run_verify_layer` paid path returns real check results only after tasks 7/8, which are landed; failures here should be assertion-shaped, not plumbing-shaped).
- [ ] **Step 3: Implement** the table, the injection helper (a small `_paid_pair(pair, advtests, judge)` model_copy fold), and the README edits.
- [ ] **Step 4: Full suite including the docker matrix and ruff.** `python -m pytest -q -m docker tests/test_hack_fixtures.py && python -m pytest -q && ruff check .`
- [ ] **Step 5: DECISIONS row, review gate, commit.** Brief the reviewer on: keeping the deterministic table intact versus replacing it (the spec's exit criterion 2 wording is satisfied by the paid table), and the injected-shape column encoding.

```bash
git add tests/test_hack_fixtures.py tests/fixtures/hacks/README.md tests/fixtures/hacks/h5-hardcoded/README.md tests/fixtures/hacks/h6-special-case/README.md tests/fixtures/hacks/h7-swallow/README.md DECISIONS.md
git commit -m "test: paid-posture verdict table flips h5/h6/h7 to SUSPECT"
```

---

### Task 11: exit-criterion runs and close-out

**Size:** the measured half: live fixture flips, four real-task paid runs, ledger. One new test module; owner-driven CLI runs.

**Files:**
- Create: `tests/test_paid_live.py`
- Modify: `pyproject.toml` (the `paid` marker registration), `.superpowers/sdd/progress.md` (new wave B ledger section), `tasks/click-0001.yaml` and `tasks/rich-0001.yaml` only if a live run exposes a spec gap (owner ruling first)

**Contracts:**
- `pyproject.toml` registers `paid`; `tests/test_paid_live.py` module-level skips unless `SKEPTIC_PAID_TESTS=1` and `ANTHROPIC_API_KEY` are set (decision 10), and every test also carries `docker` and `slow` marks.
- `test_paid_live_flips_h5_h6_h7_to_suspect`: `make_minirepo_task(tmp_path, extra_variants=[("h5", "hacked", <h5 files>), ("h6", "hacked", <h6 files>), ("h7", "hacked", <h7 files>)])` (bodies loaded via `load_hack_fixture`), then for each variant a CliRunner `verify --profile paid --yes` run; asserts exit 1, verdict SUSPECT, and records which rule crossed (advtest_divergence expected for h5/h6; for h7 the test asserts SUSPECT and captures the lever into the assertion message rather than requiring divergence, per the spec's exit criterion 1; a PASS here fails the test and the failure text says "escalate to owner").
- `test_paid_live_real_task_gold_runs_pass` is NOT written: the four real-task runs are owner-driven CLI invocations (the wave A pattern), recorded in the ledger with their exact commands, verdicts, scores, per-call usage, and wall clocks:

```bash
SKEPTIC_PAID_TESTS=1 python -m pytest -q -m paid tests/test_paid_live.py
skeptic verify --task click-0001 --variant gold        --profile paid --yes
skeptic verify --task click-0001 --variant gold-prime  --profile paid --yes
skeptic verify --task rich-0001  --variant gold        --profile paid --yes
skeptic verify --task rich-0001  --variant gold-prime  --profile paid --yes
```

- Expected per the spec: all four PASS with profile "paid" stamped; rich pair at score 0.0 (the task 1 fix measured live), click gold 0.0, click gold-prime 0.4; a judge_flag on any clean variant is recorded and escalated, never absorbed.
- Ledger close-out in the wave A form: `# SDD progress ledger: Skeptic M4 wave B` header block (plan path, branch, per-task record dir), per-task rows as they landed, the exit-criterion section with actuals (verdicts, scores, per-call `in_tok`/`out_tok`/`usd` from the traces, batch durations, docker df delta), the cut line section (used or "no cuts"), and the deferred list carried forward from the spec's parked items.
- Total spend is summed from the trace `llm_call` events and stated in the ledger with the summing command.

- [ ] **Step 1: Write the live test module and marker registration** (skip-guarded; it must collect-and-skip cleanly in the default suite).
- [ ] **Step 2: Run the default suite** to prove the zero-API property survives: `python -m pytest -q` collects the paid tests as skipped.
- [ ] **Step 3: Owner runs the live flip test and the four CLI runs** (commands above), capturing output.
- [ ] **Step 4: Record actuals in the ledger**, close the exit criterion (MET or blocked-with-escalations), full suite and ruff one last time.
- [ ] **Step 5: DECISIONS row (plan decision 10 plus any owner rulings the runs forced), final whole-branch review gate, commit.** Brief the reviewer on: the h7 lever outcome, any judge FPs, spend versus estimate.

```bash
git add tests/test_paid_live.py pyproject.toml .superpowers/sdd/progress.md DECISIONS.md
git commit -m "test: paid-lane exit criterion runs and wave B close-out"
```

---

## Self-review record

Checked against the spec section by section: scope items each map to a task (literal fix 1; paid lane 2/4/9; advtests enrichment 5/6; t2_advtests 7; judge 8; registry wiring 2/7/8; SUSPECT flip 10; exit runs 11). Exit criterion 1 lands in task 11, criterion 2 in task 10, criterion 3 in task 11, criterion 4 in every task's step 4. Type consistency: `AdvCandidate`/`AdversarialReport`/`JudgeReport` names match between tasks 3, 5, 6, 7, 8; `call_with_retry` keywords match between 4, 5, 8; `run_verify_layer(pair, profile=...)` matches between 2, 9, 10. The spec's parked list rides in the header's deferred note and the task 11 ledger contract.
