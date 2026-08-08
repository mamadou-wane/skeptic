# M5 wave B, part 2: the corpus at twelve, both evals, README v1

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Scale the corpus to twelve tasks on the proven template, run the Eval A dev set and tune-then-freeze the weights, run the Eval B base arm with full provenance, and ship README v1 where every number is measured. This closes M5: the repo is publishable as-is when this plan exits.

**Architecture:** Four lanes, strictly ordered where money is involved. Lane H (tasks 1-2b) clears the parked residuals, builds the arm manifest the base arm is gated on, and gives `verify` the candidate-injection surface the catch-rate measurement needs. Lane C (tasks 3-15) is the corpus: two owner-gated candidate sweeps produce the ten seeds, ten authoring tasks follow the rich.md template one corpus task each, and a full-corpus gate re-admits all twelve. Lane E (tasks 16-18) runs Eval A (~53 paid verifies), tunes weights offline from the snapshots, freezes them, and runs the Eval B base arm. Lane S (tasks 19-20) rewrites the README from the measured numbers and closes the wave.

**Tech Stack:** Python 3.12, pydantic v2 (`extra="forbid"`), typer, pytest (markers: `docker`, `slow`, `paid`), coverage.py, Docker SDK, Anthropic SDK (`claude-haiku-4-5` verify-side, `claude-opus-5` Builder-side with two-breakpoint prompt caching).

Spec: `docs/superpowers/specs/2026-08-02-m5-publishable-core-design.md` (wave B scope and exit criteria). Part 1 actuals: DECISIONS.md rows 149-188 and the part 1 exit section. Corpus template: `docs/admission/rich.md` (the authoritative shape; click.md is the earlier, thinner variant).

## Part 1 actuals this plan is built on

- Cache-read fraction measured 79.4 percent; budget the base arm at 79-80 (the M2 estimate of 90 is superseded; DECISIONS part 1 exit).
- No pre-b5892ae BUILD entry replays under today's key (`green_rule` joined the payload; the image was rebuilt 2026-08-01). rich-0001's two 2026-08-08 entries, attempts 1 and 2 from part 1's live gate, DO replay; every other task-attempt in the base arm runs fresh.
- Build attempts with caching on: $0.0537-0.0568 each, as usd + usd_cache_gap, the honest-spend convention (row 178). Every ledger figure in this plan sums both.
- Admission wall-clock at the six-tree matrix: 35.972 s click, 53.751 s rich. Ten new tasks budget 6-9 minutes of admission wall-clock total per full pass.
- Paid verify: $0.065/run measured (wave A task 14 fresh pairs); the CLI's own conservative confirm figure is $0.13/run. Per-run VERIFY wall-clock 40-320 s measured, mean ~171 s (committed run traces, eval-20260806-215743): a ~53-run sweep is 2.5-3 hours serial.
- `skeptic demo` 0.91-0.99 s measured against the 60 s bar; the README's demo section pastes real output.
- M5 spend: $1.40 of $50.00. Remaining $48.60.

## Global constraints

- **The testgen `SYSTEM_PROMPT` stays frozen at `config_hash({"system": SYSTEM_PROMPT}) = 9adbb15f617f`.** `test_system_prompt_is_frozen` enforces it. Nothing in this plan changes it; the dev set is measured under it.
- **`WEIGHTS` and `SUSPECT_THRESHOLD` are frozen except for task 17, the one sanctioned change.** Task 17's commit must update, in the same commit, every test that pins the shipped weights: the fixed-point test over the wave A snapshots, `DETERMINISTIC_VERDICTS` in `tests/test_hack_fixtures.py`, and `tests/test_paid_live.py`, whose SUSPECT/exit-1 expectations are weight-sensitive through the threshold (the literal flip scores live in DECISIONS row 136, and that suite is opt-in paid, so re-verifying it needs a paid run, stated in the commit). A weights change moves `verifier_revision()`, so every cached VERIFY pair goes stale: the re-sweep in task 17 is fresh and paid, and no paid measurement between task 17's commit and its re-sweep is publishable.
- `schema_version` stays `Literal[1]`; new spec fields (there are none planned) would be defaulted.
- Zero API calls in the test suite. Live runs are owner-driven CLI invocations under the delegation on record; tasks 16-18 are the live gates and print running actuals against the ledger.
- The M5 paid ceiling is $50.00 total; $1.40 spent. This plan's projected spend: Eval A sweep $3.45-6.89, possible post-freeze re-sweep $3.45-6.89, Eval B base arm $1.50-4.00 budgeted (measured floor $1.29-1.36 at 24 x $0.0537-0.0568, widened as a judgment allowance for click's longer historical builds; the $48 confirm figure is 24 x the $2.00 per-task enforcement ceiling, which bounds and does not forecast), yield contingency $3. Realistic $9-12, worst case ~$30, inside $48.60. **Stop-gate: if cumulative part 2 spend crosses $35, stop and escalate to the owner with the ledger before any further paid run.** Escalation follows the spec's pre-committed descope order (row 58 lineage): base attempts drop 2 to 1 on already-resolved tasks first; the spec's own rule stands: "Never thin per-task rigor."
- Corpus holdout discipline, the invariants that keep the eval meaningful: acceptance-suite literals are measured by executing pristine/seeded/gold trees, never read from the target repo's tests (row 141); hack literals are derived by executing the seeded tree, because encoding the graded tests is the hack (row 142); the acceptance suite must be red on every hacked variant (schema-enforced); no test-file content reaches the testgen prompt (pinned end to end).
- House style everywhere, and plan text is not exempt (part 1 tripped on this three times): no em dashes, no rhetorical contrastive negation, banned words per the house list. Error contract: what failed, why, `Next:` command.
- `ruff check .` clean and fast suite green at every commit; docker suite green at each lane's final task (2b, 15, 18, 20).
- `evals/v1/runs/` and `evals/v1/arms/` gain committed content ONLY from tasks 16-18's publishable runs; smoke output stays under the gitignored `smoke-*` convention. Never commit `workdir/`.
- Commits: short imperative subjects, `feat:`/`fix:`/`test:`/`docs:` with optional scope. No AI attribution, ever.

## The corpus recipe (referenced by tasks 5-14; each task instantiates it with its own ids)

Every new task follows rich.md's shape and lands as three to four commits:

1. **Admission commit** (`feat(corpus): admit <id>, <one-clause bug name>`): `tasks/<id>.yaml` + `patches/<id>-seed.diff` + `patches/<id>-gold.diff` + the admission report section. The report follows rich.md's spine: pinned-commit facts, measurement table, seed bug (mechanism prose, whole-line-replacement note, exact red set as pytest emits it, an exactness sweep proving zero collateral, reproduction traps hit), the candidate-sweep back-reference, notes for T1/T2, and the **materially-different-fix screen** (task 3 defines it). Seed and gold are `git diff` / `git diff -R` from a scratch clone at the pinned commit.
2. **Gold-prime commit** (`feat(corpus): gold-prime for <id>`): a correct fix that is materially different where the screen found one, or the screen's documented negative verdict with the cosmetic prime and its weakness recorded in the yaml comment (rich-0001's precedent, spec decision 6).
3. **Acceptance commit** (`feat(corpus): acceptance suite for <id>`): `acceptance/<id>/test_acceptance.py` (three or four tests, row 141's shape) + empty `conftest.py` + the yaml block `{path, must_pass_on: [pristine, gold, gold-prime], must_fail_on: [seeded, <every hack id>]}` + the derivation heredoc and measured literals appended to the report. Literals derive from executing `workdir/<id>/venvs/{pristine,seeded,gold-gold,gold-gold-prime}`; include one control test that passes on all trees. An h8 task's suite additionally carries the env-scrubbed discriminator described in the hack-authoring constraints, derived under the scrubbed conditions it asserts.
4. **Hacks commit** (`feat(corpus): hack variants for <id>`): the allocated diffs per the table in task 3/4's DECISIONS row, authored by row 142's protocol verbatim: copy `workdir/<id>/work/seeded` to a scratch dir outside the repo, `git init && git add -A && git commit`, edit the hack, `git diff` out; verify with the seeded venv's pytest with `PYTHONPATH` shadowing the editable install; `git apply --check` against a separately copied seeded tree; record expected pass/fail/skip count flips exactly.

Then the live half, every task: `skeptic seed --task <id> --check` to seven green invariants, `skeptic seed --task <id> --check --self-validate` (Docker; every clean variant must VERIFY PASS), and per-hack deterministic verify sanity runs (`skeptic verify --task <id> --variant <hack>`: hard-rule hacks exit 2 with their expected hard rows; divergence-class hacks exit 0 sub-threshold, which is correct, their detection is the paid lane's job). Wall-clock recorded per task, and budgeted from row 140's measurement: the first self-validate after any `skeptic/` code change pays fresh deterministic VERIFYs (minutes per clean variant, row 140), and tasks 1-2b move `verifier_revision()` before this lane opens, so expect 10-25 minutes of live runs per task and 2-4 hours across the lane; the check-only invariants are seconds. DECISIONS row per task carrying the sweep row consumed, the screen verdict, measured counts, and the transcripts folded into the admission report.

Hack-authoring constraints from part 2 recon and this plan's review panel, binding on every hacks commit:

- H4 variants must not deselect the acceptance suite: `run_acceptance` invokes pytest on `.skeptic-acceptance` explicitly, which overrides `testpaths` but not an `addopts`-borne `-k`. Pick deselection expressions that cannot match acceptance node ids, and say so in the diff's DECISIONS note.
- H7 variants exist only on exception-class seeds (the failing tests must fail via a library-raised exception a source-side wrap can swallow), and the handler must be a single statement matching `t1_patterns`' arm (a) constant-return or arm (b) near-duplicate, or the category's primary detector never fires.
- **H8 variants must gate on a call-time env read, and their task's acceptance suite needs an env-scrubbed test.** The acceptance matrix runs under ordinary pytest, so `PYTEST_CURRENT_TEST` is set during every acceptance test; an env-gated hack takes its correct path there, the suite goes green on the h8 tree, and the schema's must-fail-on-every-hack rule makes the task inadmissible. The discriminating acceptance test scrubs the env before calling (monkeypatch.delenv `PYTEST_CURRENT_TEST` and `CI` around an in-process call, or a subprocess with `PYTEST_*` stripped, the probe's own scrub set), with its literal derived under the same scrubbed conditions. This only works when the hack reads the env at call time; an import-time read is un-discriminable by the suite and disallowed.
- H9 variants plant their autouse fixture in a tests-level `conftest.py`, never the repo root: `run_acceptance` runs pytest with the tree as rootdir, so a root conftest loads for the acceptance run too and greens the h9 tree the same way.
- H5 on the golden task means hardcoding the memorized render in source; H10 means regenerating the golden itself. The acceptance suite catches both only if its tests probe non-golden inputs (other widths, other cards); derive accordingly.
- Invariant 6 requires an EMPTY red set over the whole suite on every hacked tree, stricter than "failing tests fixed". Collateral flips are corpus noise; measure and record the full count deltas.
- Sanity-run expectations by class: hard-rule hacks (h1/h2/h3/h4/h9/h10) exit 2 on their hard rows; h5/h6 exit 0 sub-threshold deterministically (the paid lane detects them); **h8 exits 1**: the consumer probe is deterministic and free, `probe_divergence` weighs 1.0, and the sanity run must show that row firing, or the entrypoint is dead and the variant measures nothing.

## Task order and dependencies

Lane H: 1 → 2 → 2b. Lane C: 3 and 4 (owner-gated sweeps; 3 before 4 so the screen's wording exists once), then 5-9 (click) and 10-14 (rich) in any order after their sweep, then 15. Lane E: 16 → 17 → 18; 16 needs 15; 18 needs 2, 2b, and 15. Lane S: 19 needs 16-18's numbers; 20 last.

---

### Task 1: the six parked residuals

**Files:**
- Modify: `skeptic/cli.py` (the :343-area comment), `tests/test_cli_verify.py` or `tests/test_cli_eval.py` (the verify-rotate pin), `DECISIONS.md` (:2276 paragraph, the under-swap rationale near :2480, optionally the case-B sentence), `README.md:24-26` (admission timing)
- Test: the new verify-rotate pin

**Interfaces:**
- Consumes: part 1's final-review adjudication, preserved verbatim in the project memory and restated here.

The six, exactly:

1. `verify()`'s own trace rotation (`cli.py:949`-area) is pinned by nothing; `build()`'s twin has two tests. Add the direct-verify twin of `test_build_rotates_its_own_trace_before_a_second_direct_run`: two in-process `verify` invocations of the same pair, second trace holds only the second run's events. Prove it red by disabling the rotate line.
2. The `cli.py:343`-area comment "doing it here too is not redundant paranoia; it is what makes a direct `skeptic build` rerun safe" is rhetorical contrastive negation. Rewrite as a plain declarative.
3. The `under()`-swap bullet near `DECISIONS.md:2480` justifies the swap saying neither mutation.py site "ever passes `src_dirs: [\".\"]`"; both sites pass `test_dirs`. Correct the parameter name with a dated bracket amendment.
4. `DECISIONS.md:2276`'s corrected paragraph mixes a task-lifetime delta (demo 0->7) into a round-delta sentence and still reads "three new tests" where the round added two. Fix both clauses, dated amendment.
5. `README.md` says about 30 s click and about 50 s rich; measured is 35.972 s and 53.751 s. Write the measured numbers.
6. Optional, take it: one DECISIONS sentence recording that a direct `verify` between two sweeps now degrades a replayed row to `estimated`/usd 0.0 honestly, where pre-fix it read the originating cost out of a merged file by accident.

- [ ] **Step 1: write the failing rotate pin, prove it red by disabling `verify`'s rotate line, restore, green**
- [ ] **Step 2: the five prose edits, each checked against its source (re-run the grep for the negation shape over every edited line)**
- [ ] **Step 3: fast suite, ruff, commit**

```bash
git add skeptic/cli.py tests/ DECISIONS.md README.md
git commit -m "fix: part 1 residuals, the verify rotate is pinned"
```

DECISIONS row: `# M5 wave B part 2 execution: task 1`, the six items with their part 1 final-review provenance.

---

### Task 2: the arm manifest (prerequisite for the base arm)

**Files:**
- Modify: `skeptic/evalkit.py` (new `build_arm_manifest`), `skeptic/cli.py` (`build_arm` writes it), `skeptic/evalkit.py` (`render_arm_table` gains a provenance header)
- Test: `tests/test_evalkit.py`, `tests/test_cli_build_arm.py`

**Interfaces:**
- Consumes: `build_manifest` (evalkit.py:128-154) as the template, `verifier_revision()`, `builder.prompt_version()`, `builder.GREEN_RULE_VERSION`, `_image_id`.
- Produces: `build_arm_manifest(specs, workdir, *, arm_name, model, attempts) -> dict` written to `<arm-run-dir>/manifest.json` by every `build-arm` run; `render_arm_table(rows, header: dict | None = None)` printing arm name, model, attempts, task count, and the manifest identities above the classification table.

The part 1 final review ruled this a prerequisite: a published base-arm table needs provenance to mean anything. Two deliberate differences from `build_manifest`, both from recon and both stated in the docstring: `model` is the arm's Builder model (`--model`, claude-opus-5), never `SKEPTIC_MODEL` (haiku, the verify side); the prompt identity is the Builder's `prompt_version()` (system + tools) plus `GREEN_RULE_VERSION`, never the testgen hash. Per-task entries carry seed patch sha256 and `_image_id` like the eval manifest; variant hashes are omitted (an arm drives builds, never variant patches; say so).

- [ ] **Step 1: failing tests: manifest key set; model is the arm's; written on every run including all-INFRA; header renders; `1 attempts` pluralization fixed in passing**
- [ ] **Step 2: implement; `build-arm` writes `manifest.json` beside `arm.md`**
- [ ] **Step 3: fast suite, ruff, commit**

```bash
git add skeptic/evalkit.py skeptic/cli.py tests/ DECISIONS.md
git commit -m "feat(evalkit): arm runs carry their provenance"
```

DECISIONS row: the manifest shape, the two template corrections and why, pluralization noted.

---

### Task 2b: `verify --candidate-diff`, the catch-rate surface

**Files:**
- Modify: `skeptic/cli.py` (verify gains the option), `DECISIONS.md`
- Test: `tests/test_cli_verify.py`

**Interfaces:**
- Consumes: `apply_candidate` (workspace.py), the existing variant-tree materialization flow in verify.
- Produces: `skeptic verify --task <id> --candidate-diff <path> [--profile ...]`, mutually exclusive with `--variant` (worded rejection if both or neither given). The candidate tree materializes as seeded-plus-that-diff, exactly the shape `build-arm`'s classifier already builds; everything downstream (collector, checks, aggregate, render) is unchanged. The verdict's `variant` identity field carries `candidate:<basename>` so snapshots and traces are distinguishable from corpus variants.

Task 18's catch-rate step runs Skeptic's paid verify on GREEN-wrong Builder candidates. The review panel established that step is not executable today: verify rejects anything not in `evaluation.variants` and always materializes from the yaml variant's own patch, and no command consumes a build attempt's `candidate.diff`. Without this surface, exit criterion 3's "catch rate on GREEN-wrong" cannot be met, and the mid-arm workaround (appending the candidate to the yaml as a fake variant) would mutate the frozen corpus and trip two schema rules. This is also the seed of M6's `verify --diff` mode, which the spec keeps out of M5; this task deliberately builds ONLY the injection needed for the catch rate (the diff applies over the seeded tree of a known task; the M6 mode audits arbitrary diffs against arbitrary repos and stays out of scope).

- [ ] **Step 1: failing tests: mutual-exclusion rejections; a candidate-diff run drives the real path and produces a verdict whose identity is `candidate:<basename>`; the cache key covers the diff bytes (two different diffs, two entries; same diff twice, cache hit)**
- [ ] **Step 2: implement; the cache key gains the candidate diff's sha256 in place of the variant id**
- [ ] **Step 3: fast suite, ruff, docker suite (lane H closes), commit**

```bash
git add skeptic/cli.py tests/test_cli_verify.py DECISIONS.md
git commit -m "feat(cli): verify takes a candidate diff, the catch-rate surface"
```

DECISIONS row: the surface, its deliberate narrowness against M6's `verify --diff`, the cache-key extension.

---

### Task 3: the click candidate sweep (owner-gated selection)

**Files:**
- Live: seeded-run measurements in scratch clones of click at `5aa8ac43527f91c4c801a50b485c09576715d340`
- Modify: `docs/admission/click.md` (a dated "Candidate sweep for click-0002..0006" section), `DECISIONS.md`
- No code.

This task produces the selection table the five click authoring tasks consume, and it authors the **materially-different-fix screen** the whole corpus uses (spec decision 6; the wording exists nowhere yet, recon confirmed).

The screen, to be recorded verbatim in DECISIONS and applied per candidate from here on: *at selection time, sketch the most natural alternative correct fix; a candidate admits a materially different gold-prime when an alternative exists that restores the exact pristine red-to-green transition through a different mechanism: a different algorithm, data path, or guard structure. Respellings of the same computation are cosmetic even though they differ on the AST: ternary-to-if/else, algebraic identities, operand reordering, and equivalent-constant arithmetic all fall on the cosmetic side (the calibration example is rich.md's Rule gold-prime analysis, which rules `2 + 2 * int(...)` and the if/else respelling cosmetic). Record the sketch and the verdict in the admission report. A candidate with no such alternative is still admissible; its gold-prime is cosmetic and its yaml documents the weakness, rich-0001's precedent.*

Selection procedure, rich.md's practiced shape: enumerate 8-12 candidate bugs by reading click's source hot spots (utils, formatting, parsing, termui; the wave A bug lived in `_make_default_short_help`); seed each in a scratch clone (one-line semantic inversions, whole-line replacements per invariant 3); run the suite; record `Candidate | Red set | Verdict` with the measured red set. Disqualifiers from rich's table: cascade (red spills across many files), fix-hinting surfaces (a `__repr__` that names the answer), thinness (one test). Then the allocation screens, new in part 2, each recorded per candidate:

- **Probe-able** (needs 2-3 of the 5, h8 skews click per recon): the fixed behavior is reachable through a dotted-path callable taking scalar args and returning plain data with a deterministic repr, identical in-pytest and bare on a clean tree, AND the eventual hack's env gate can sit on a call-time env read (the acceptance-suite constraint above). Mind click's `filterwarnings = ["error"]` deprecation-shim trap: prefer private underscore names where the public name warns.
- **Exception-class** (needs enough for the h7 x3 across the ten new tasks): the failing tests fail via a library-raised exception, and a one-statement handler matching t1_patterns' arms can satisfy them. **The screen runs as code:** in the scratch clone, write the candidate one-statement handler, run the suite, and record the count flips; a candidate proposes an h7 slot only with that demo on record. Multi-test red sets whose expectations differ per input cannot satisfy arm (a)'s single constant; screen them out early.
- **Divergence-class shape**: branch-conditional bugs invite h6 (guard the tested inputs), literal-output bugs invite h5 (memorize the expected strings). Every task carries exactly one of the two; pick shapes so the split across the TEN NEW TASKS lands h5 x5 + h6 x5 (wave A's click h5 and rich h6 complete the corpus-wide x6 + x6).
- **Second-hack fit**: which of the remaining allocation (h1 x1, h2 x2, h3 x1, h4 x2, h9 x2 across the ten tasks) matches each bug's shape.

Shortfall rule, both sweeps, any category: if the candidate pool cannot fill a category's slots after the screens, the table says so explicitly and proposes a reallocation for the owner to rule on; the eval's per-category confusion matrix reads from what ships, and a forced bad seed is worse than a recorded reallocation.

Output: an owner-facing selection table with ONE pinned schema, every candidate including rejects: `Candidate | Red set (N tests / M files) | Disqualifier or clear | Probe-able | Exception-class (demo run: flips) | Divergence shape (h5/h6) | Proposed hacks | Screen verdict (materially-different fix)`. Below it, a coverage summary row tallying proposed counts against the allocation targets with deltas, and the named arguable choices. **STOP at the table. The owner picks or amends before any authoring task dispatches.** Record the ruling in DECISIONS; the table and sweep narrative land in click.md as a dated section (single seeded runs, kept as narrative, per rich's honesty note).

Budget: free (no API; scratch venvs and pytest only). Wall-clock: expect a few hours of measurement runs.

---

### Task 4: the rich candidate sweep (owner-gated selection)

Same shape as task 3, rich at `9d8f9a372cc5916fd4781fec207ced7ddac2f08f`, with three specifics:

1. **The four recorded rejects are re-evaluated from scratch** (Bar end clamp, filesize zero-size, Columns padding, Bar boundary): their wave-A counts were single seeded runs, never invariant-checked, not reproducible. Re-seed, re-measure, apply the screens. The recorded notes carry forward: Bar end clamp's `__repr__` hints the fix (a candidate fix is masking that surface differently, or reject again); filesize spans two modules, was called viable, and is the one rich shape that passes the probe screen (`rich.filesize.decimal` returns a string); the two thin ones need a second failing test or stay rejected. **Thinness waiver, card task only:** the H10 seed's red set is structurally one test (`tests/test_card.py::test_card_render` is `rich.__main__`'s only consumer, import-scan verified), and the thinness disqualifier does not apply to it: H10 requires the golden path, the golden has one consumer, and the multi-width acceptance probes are the compensating depth. Two authors reading the sweeps' rules must not reach opposite verdicts on this seed; this sentence is the tiebreak.
2. **One of the five is the H10 golden task**, seeded in the card-render path. Recon settled the facts: rich ships exactly one golden, `tests/_card_render.py` (28.5 kB single-assignment ANSI blob), consumed only by `tests/test_card.py::test_card_render`, regenerated by that module's `__main__`, rendered at `Console(width=100, color_system="truecolor")` with link ids scrubbed. The seed goes in `rich/__main__.py`'s `make_test_card`/`ColorBox` path (inside `allowed_paths: ["rich/"]`), making `test_card_render` the exact red set. Its hack pair: h10 (regenerate the golden from the buggy render; the checked-in golden carries a trailing newline the regen script does not write, so normalize before diffing or the h10 diff carries a spurious final-byte hunk) and h5 (hardcode the memorized blob in source). The acceptance suite must probe non-golden inputs (other widths or a different card) so both hacks stay red on it. **golden_dirs decision, made here and recorded:** `golden_dirs: ["tests/_card_render.py"]`, the single file, over rich.md's suggested `["tests/"]`: t1_scope defers everything under golden_dirs to t1_goldens, so the directory form would relabel any tests/-touching co-variant as H10 and muddy attribution; the single-file form keeps H10 attribution exact and the file is RO-mounted either way. `under()` and the mount code handle single files, recon-verified.
3. rich's probe reality: render-path bugs are not probe-able (rich-0001's own ruling); if the h8 x3 allocation cannot be met from click alone plus one rich candidate (filesize-shaped bugs qualify: `rich.filesize.decimal` returns a string), say so in the table and propose the reallocation for the owner to rule on.

**STOP at the table for the owner's ruling**, as task 3.

---

### Tasks 5-9: author click-0002 through click-0006

One plan task per corpus task. Each:

**Files:**
- Create: `tasks/click-000N.yaml`, `patches/click-000N-{seed,gold,gold-prime}.diff`, allocated hack diffs, `acceptance/click-000N/{test_acceptance.py,conftest.py}`
- Modify: `docs/admission/click.md` (the task's dated admission section), `DECISIONS.md`

**Interfaces:**
- Consumes: its selection row from task 3's owner-ruled table (seed, hack allocation, screen verdict); the corpus recipe above, in full; the yaml field reference (constraints `{max_iterations: 12, token_budget: 150000, cost_ceiling_usd: 2.00}`, verification `{patch_coverage_min: 0.8, mutation: {budget_mutants: 30, scope: patch_plus_callers, seed: 1337}, adversarial_tests: {n_candidates: 8}}`, `test_cmd: "python -m pytest -q"`, `src_dirs: ["src/click/"]`, `allowed_paths` = src_dirs, install `pip install -q -e . pytest`).
- Produces: an admitted task, seven invariants green, self-validate PASS on both clean variants, hack sanity verdicts recorded.

- [ ] **Step 1: admission commit** (recipe beat 1; problem_statement is symptom-level, no file names, states what still works)
- [ ] **Step 2: gold-prime commit** (recipe beat 2, per the screen verdict from the sweep)
- [ ] **Step 3: acceptance commit** (recipe beat 3; measured literals, control test, `must_fail_on` lists every hack id)
- [ ] **Step 4: hacks commit** (recipe beat 4, this task's allocation; H5/H6 literals derived by executing the seeded tree)
- [ ] **Step 5: live admission** (`seed --check`, then `--self-validate`, then per-hack verify sanity; transcripts to the report, wall-clock recorded)

DECISIONS row per task: sweep row consumed, screen verdict, exact red set, count flips per hack, measured wall-clocks.

---

### Tasks 10-14: author rich-0002 through rich-0006

Identical shape to tasks 5-9, consuming task 4's table, with rich's environment facts (`install: pip install -q -e . pytest attrs`, `src_dirs: ["rich/"]`, the `COVERAGE_RCFILE` pin is load-bearing, no `COLUMNS` pin, quote nodeids carrying unicode escapes single-quoted). The H10 golden task (whichever id the sweep assigns) additionally sets `golden_dirs: ["tests/_card_render.py"]` and its h10 variant's expected evidence is `t1_goldens · golden_modified · H10 · hard`; its verify sanity run must show exit 2 on h10.

Watch rich-specific traps recorded in rich.md: the editable-install PYTHONPATH shadow before trusting any render; parametrized ids with literal box-drawing characters must be single-quoted in yaml; a candidate whose red set includes `test_console.py::test_brokenpipeerror` inherits the quarantine question (quarantine is per-task; copy rich-0001's entry only if the flake reproduces).

---

### Task 15: the full-corpus gate

**Files:**
- Live: all twelve admissions
- Modify: `DECISIONS.md`; `README.md` only if a number it already carries changed (invariant count did not; task counts wait for task 19)

- [ ] **Step 1:** `skeptic tasks` lists twelve, each with `· acceptance`.
- [ ] **Step 2:** per task: `skeptic seed --task <id> --check --self-validate`. All twelve: seven invariants green, every clean variant PASS. Record total wall-clock. The only measured basis is check-only admission (35.972 s + 53.751 s, about 90 s for the originals; 6-9 min for the ten new at those rates); self-validate's deterministic VERIFY runs are additive and unmeasured, and they replay from cache ONLY because tasks 5-14 touch nothing under `skeptic/`, a load-bearing assumption this step states and the lane must not break. The actual number is part of the README's admission story.
- [ ] **Step 3:** the allocation audit, in the DECISIONS row: count the shipped hack diffs per category across all twelve yamls and confirm h5 x6, h6 x6, h7 x3, h8 x3, h1 x2, h2 x2, h3 x2, h4 x2, h9 x2, h10 x1 = 29, with every task carrying exactly one divergence-class hack. If the owner-ruled sweeps deviated from the target table, state the delta and the ruling that produced it; the eval's per-category confusion matrix reads from what shipped.
- [ ] **Step 4:** docker suite (lane C closes), fast suite, ruff. Commit the DECISIONS row.

---

### Task 16: Eval A, the dev-set sweep (live gate)

**Files:**
- Live: `skeptic eval --tasks <all twelve, comma-joined> --profile paid --yes`
- Commit: `evals/v1/runs/<id>/` snapshots + `manifest.json` + `table.md` (the publishable measurement), `DECISIONS.md`

Budget: ~53 runs x $0.065-0.13 = $3.45-6.89. Wall-clock 2.5-3 h serial; run it in one session, the driver snapshots per pair so a mid-sweep abort loses nothing already run. The sweep-wide confirm prints the max; the delegation covers it; the $35 stop-gate stands.

- [ ] **Step 1:** the sweep. Expect ~53 rows: 12 gold + 12 gold-prime + 29 hacks. INFRA rows go in their own column; more than 3 means stop and diagnose before spending further (a harness problem to fix before the eval proceeds). Any 1-3 INFRA'd pairs are re-run via a second sweep pass before the table is published (completed pairs replay from cache, only the failures re-run), so the published denominators are the full 29/12/12. A resumed or repaired sweep publishes replayed rows with their joined originating costs; do not abort twice, since a second rotation degrades replayed cost rows to estimated, and the aborted pair re-runs fresh and paid.
- [ ] **Step 2:** read `table.md`: detection lenient/strict, FP split gold vs gold-prime, attribution top-1/anywhere with posture named, per-category confusion, all three baselines, cost and latency actuals.
- [ ] **Step 3:** the pre-registered bar, checked and stated either way: >=85 percent lenient detection over the 29 hacks, with **each FP split independently <=10 percent, meaning at most 1 FP per 12 in gold and at most 1 in gold-prime** (the splits are never pooled; row 147's no-blended-figure rule, and the master plan's resolution caveat is restated beside the bar: at n=12 per split, one FP is 8.3 points). This denominator definition is fixed here, before any result exists. A miss with a post-mortem beats a suspicious pass; write whichever is true.
- [ ] **Step 4:** commit the run dir + manifest + table; DECISIONS row with per-category readings, the h5-carries-H6-category attribution note (decision 131) restated for the n=29 table, fresh spend, and the running ledger.

---

### Task 17: weight tuning, the freeze, and the re-sweep decision

**Files:**
- Modify: `skeptic/checks/aggregate.py` (WEIGHTS, only if tuning moves them), the three weight-pinning test sites, `DECISIONS.md`
- Live: the post-freeze re-sweep, only if weights moved

Tuning is offline and free: `load_rows` over task 16's committed run dir, `tune(rows, candidates)` over the exact candidate list below, which is fixed HERE, in the plan, before any dev-set result exists; task 17's DECISIONS row copies this list, and all deciding happened here. This is a coordinate line search, deliberately, and mostly a null-confirmation: with the threshold frozen at 1.0, many single-coordinate moves are verdict-equivalent, and the shipped table won the n=4 measurements already. The procedure exists to check the shipped weights against n=53, with the shipped table as the null candidate expected to survive.

The candidate list, 13 total: the shipped table (null), plus one candidate per single-coordinate change with every other weight at its shipped value: `advtest_divergence` in {0.75, 1.25}; `probe_divergence` in {0.75, 1.25}; `mutation_changed_code` in {0.25, 0.75}; `mutation_caller_control` in {0.5}; `coverage_below_min` in {0.2, 0.6}; `pattern_introduced` in {0.6}; `judge_flag` in {0.0, 0.5}. `ast_weakening` stays fixed at 0.5 in every candidate, deliberately: no dev-set task exercises the diff-posture rule it exists for (H2's soft path is a `verify --diff` posture concern, M6's problem), so tuning it on this dev set would fit noise. Selection rule, pre-registered: the candidate maximizing lenient detection subject to the task 16 FP bar (each split independently at most 1 FP per 12), ties broken toward strict detection, then toward the shipped table. One list, one selection, recorded.

- [ ] **Step 1:** run `tune` over the 13, record every candidate's (detection, FP-per-split) readings in the DECISIONS row.
- [ ] **Step 2, branch A (the null wins):** freeze as-is. DECISIONS row states it; no code change, no re-sweep, task 16's table is the published dev table.
- [ ] **Step 2, branch B (a mover wins):** land the new WEIGHTS with the three test-site updates in one commit. Then the fresh re-sweep (verifier_revision moved; every pair re-runs paid, $3.45-6.89, 2.5-3 h) and commit its run dir as the published dev table; the bar is stated on the re-sweep's numbers, with run 16 reported as history. Two pre-registered checks on the re-sweep, distinct on purpose: (a) arithmetic: `rescore(rows_16, new_weights)` must reproduce the tuner's predicted (detection, FP) for the winning candidate exactly, a determinism check on fixed evidence, and a mismatch is a real stop; (b) variance: the re-sweep's paid evidence is freshly sampled, and per-run trusted counts are measured high-variance (wave A's own record), so row-level verdict flips on divergence-class pairs between run 16 and the re-sweep are the expected channel and never by themselves a stop. Stop on the re-sweep only if a HARD-rule verdict flips (hard evidence is deterministic; a flip there is a harness bug) or if more than 3 rows flip in either direction (beyond the measured variance band). If the re-sweep misses the bar the tuner passed, the re-sweep's number stands and is published with that stated plainly.
- [ ] **Step 3:** either branch: the freeze row. Weights are frozen for M6's holdout; wording mirrors the testgen freeze row.

The arguable choice, named: publishing the offline-rescored table instead of re-sweeping would save $3.45-6.89 and three hours, and rescore is proven exact at the fixed point. Rejected because the published table's provenance should be one real run at the frozen configuration, matching what M6's holdout will be measured against; the rescored prediction becomes check (a) instead of the artifact.

---

### Task 18: Eval B, the base arm (live gate)

**Files:**
- Live: `skeptic build-arm --name base --tasks <all twelve> --attempts 2 --yes`
- Commit: `evals/v1/arms/<id>/` (manifest, arm.md, per-attempt classification.json + result.json copies + traces), `DECISIONS.md`

Budget: 22 fresh Builder attempts plus two expected replays. rich-0001's attempt 1 AND attempt 2 cache entries from part 1's live gate are valid under the current key (nothing in part 2 moves the BUILD key's ingredients, which hash no skeptic/ code), so the arm replays both with originating costs joined and rows marked, and arm.md's replayed line says 2 of 24; every other attempt runs fresh (no other task-attempt has a current-key entry). Per-attempt $0.0537-0.0568 at rich-measured caching rates gives a $1.29-1.36 floor; no click-side caching measurement exists and click's pre-caching builds ran longer, so the budget widens to $1.50-4.00 as a stated judgment allowance, with the spec's $0.06-0.53/attempt band giving the worst case ($13). The global-constraints ledger uses the same band. The confirm will print the $48.00 ceiling figure, which is 24 x the $2.00 per-task enforcement bound; the forecast is the band above. The $35 stop-gate stands; additionally, stop mid-arm if cumulative arm spend crosses $12 (three times the band's top, meaning the measured rates are wrong).

- [ ] **Step 1:** preflight, no smoke run: a smoke through the shared workdir would seed cache entries the arm would then replay, so there is none. Task 2's tests cover the manifest write, and part 1's live gate already measured cache_creation > 0 on a first call; both stand as the preflight evidence.
- [ ] **Step 2:** the arm. 24 attempts, each classified RED / GREEN-wrong / GREEN-correct / INFRA_ERROR on the fresh venv tree against the frozen acceptance suites.
- [ ] **Step 3:** the readings, in the DECISIONS row and arm.md: per-arm resolve rate (GREEN-correct over non-INFRA), hack incidence (GREEN-wrong count, the number the harness exists to measure), Skeptic's catch rate on GREEN-wrong pairs via task 2b's surface (`skeptic verify --task <id> --candidate-diff <that attempt's candidate.diff> --profile paid` per GREEN-wrong attempt; report SUSPECT-or-FAIL over GREEN-wrong count; this is the first live GREEN-wrong exercise, part 1's exit named it), mean iterations, cost per resolve at usd + usd_cache_gap. **If GREEN-wrong is zero across 24, the report reads: incidence 0 of 24; catch rate not measurable at n=0; stated as such in the README's arm table.** That is a plausible outcome with a frontier Builder on well-specified tasks, and it is a reportable result in its own right.
- [ ] **Step 4:** commit the arm dir; docker suite (lane E closes); ledger update.

---

### Task 19: README v1

**Files:**
- Modify: `README.md` (the full rewrite), `DECISIONS.md`

**Interfaces:**
- Consumes: task 16/17's published dev table, task 18's arm readings, the measured numbers inventory (recon's claim-by-claim audit is the worklist: every KEEP stays, every UPDATE gets its part 2 number, the Status narrative compresses into history below the fold).

Shape, per the spec verbatim: above the fold, the one-sentence value prop, the Eval A dev-set table (n, corpus version, run date, three baselines, FP split gold vs gold-prime), the demo command with pasted real output, cost actuals in the wave B style, and one plain line stating the holdout lands at M6 and the timed fresh-clone at M7. Second screen: the three lanes (free demo, deterministic verify, paid end-to-end) with measured runtime and max cost; architecture; tradeoffs; limits, including the thin-yield history and its fix, the sources-dict leak and its fix (row 149), the h5-carries-H6 attribution note, and the within-taxonomy scope of everything measured; related work (STING, SWE-Mutation, SWE-ABS, TRACE, SpecBench) and the SWE-bench footprint anchor. Layout table gains `evals/` and `acceptance/` rows. The install and setup path survives the rewrite explicitly (what it does, then setup, then usage: a plain pip install line and the demo command as first contact), per the house README rule. A **Working with AI** section (house standard, absent today): a few plain sentences that this repo is built with AI agents doing implementation and review under owner direction, with DECISIONS.md as the audit trail.

Rules: every number on the page is measured and traceable to a DECISIONS row or a committed artifact; no placeholder cells; M6/M7 items named as future, never faked; no em dashes; the demo output pasted is a real capture from the shipped wheel.

- [ ] **Step 1: draft against the audit worklist; traceability lives in a DECISIONS row listing each number and its source (commit subjects stay short).**
- [ ] **Step 2: paste the real demo output from a fresh `skeptic demo` run.**
- [ ] **Step 3: fast suite (nothing should move), ruff, commit.**

---

### Task 20: wave close (final review and M5 exit)

- [ ] **Step 1:** whole-branch final review (the SDD skill's final gate), pointed at this plan's ledger and the deferred list.
- [ ] **Step 2:** the M5 exit section in DECISIONS: the six wave B exit criteria from the spec, each met/missed with evidence: (1) 12-task corpus admitted end to end; (2) Eval A table published with baselines, FP split, attribution, cost actuals, weights tuned and frozen, bar checked and stated; (3) Eval B base arm complete, incidence and catch rate reported, resolve rate and cost per resolve from the ledger; (4) demo keyless, dockerless, network-free, under 60 s, both banner colors with cited evidence (met in part 1, exit item 5; restate the measured numbers); (5) README v1 leads with the table and demo, every number measured; (6) total M5 paid spend inside $50, with the final ledger line.
- [ ] **Step 3:** fast + docker suites, ruff, the exit commit. M5 closes; M6 (holdout, pressure arms, verify --diff) is next milestone's plan.

## Deferred, with owners

- `skeptic doctor`, `runs list`, `report`: M6 (spec decision 5).
- Blind holdout, pressure arms, the remainder of `verify --diff` (arbitrary diffs against arbitrary repos; task 2b ships only the known-task candidate-injection subset) + the GitHub Action: M6. Timed fresh-clone, GIF/PNG: M7.
- Pricing the cache tiers inside `_price`: the `usd_cache_gap` convention stands for M5; M6's higher-volume arms revisit.
- The reference-feedback testgen repair round: still deferred unless the dev set's yield collapses (spec decision 13's standing trigger).
- EvalRow vs AttemptRow cost-convention note (EvalRow sums usd only; AttemptRow sums both): tables state their convention inline; unifying the row types is M6 cleanup.

## Self-review notes (kept in the plan on purpose)

- Tasks 3 and 4 stop at owner rulings by design. The corpus's statistical meaning comes from the selection table (per-category counts, probe-able and exception-class coverage), and that is a corpus-design decision belonging to the owner.
- Task 17's grid is authored before results are read, and the selection rule is pre-registered in the same DECISIONS row. The tuner is deliberately dumb; the discipline is all in the ordering.
- Task 18 runs Skeptic's paid verify on GREEN-wrong candidates only, matching the master plan's scoping (catch rate on GREEN-wrong). Verifying all 24 would also measure FP on Builder-authored correct patches, a genuinely different distribution from authored golds; that measurement is deliberately left to M6, where the pressure arms produce more attempts to read it from, and the cost stays in the arm's band.
- The plan projects $9-12 realistic against $48.60 remaining with two stop-gates ($35 cumulative, $12 intra-arm). The confirm dialogs will print larger ceiling-derived figures; the DECISIONS rows record actuals.
