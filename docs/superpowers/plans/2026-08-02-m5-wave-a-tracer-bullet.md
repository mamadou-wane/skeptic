# M5 wave A implementation plan: the tracer bullet

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove the whole M5 pipe on the two existing tasks: acceptance machinery, hack variants, the adversarial-test yield fix, and an evalkit that emits a real n=2 Eval A mini-table with all three baselines.

**Architecture:** Three lanes. Corpus lane (tasks 1-6): additive schema, acceptance-suite invariant, invariant-4 automation, then real suites and hack variants for click-0001 and rich-0001. Yield lane (tasks 7-10): observability, parse filter + top-up retry, prompt calibration + one-hop context, then owner-driven live iteration to the measured bar and prompt freeze. Eval lane (tasks 11-14): eval driver with collision-safe snapshots, pure-reader metrics, baseline rows, then the owner-driven n=2 mini-table that closes wave A.

**Tech Stack:** Python 3.12, pydantic v2 (`extra="forbid"` everywhere), typer, pytest (markers: `docker`, `slow`, `paid`), coverage.py, Docker SDK, Anthropic SDK (`claude-haiku-4-5` verify-side).

Spec: `docs/superpowers/specs/2026-08-02-m5-publishable-core-design.md`. Decision provenance: every task that changes a recorded contract lands its DECISIONS.md row in the same commit, owner-approved at task review.

## Global constraints

- `schema_version` stays `Literal[1]`: every new spec field is defaulted (DECISIONS row 98). No field goes mandatory in wave A.
- `extra="forbid"` on every pydantic model; frozen evidence schema untouched (no new rules, weights, or severities in wave A).
- Zero API calls in the test suite. Live runs are owner-driven CLI invocations with confirmation; actuals go to the ledger (`.superpowers/sdd/2026-08-02-m5-wave-a/progress.md`).
- The testgen trust ladder's rungs are untouchable: reference, target_coverage, seeded_green, gold_prime semantics do not move (spec §Yield lane).
- `build_testgen_prompt` keeps its exact two-parameter signature; `tests/test_testgen.py`'s `inspect.signature` pin stays green unmodified.
- M5 paid ceiling $50 total; wave A live halves budget ≤$12 (tasks 7, 10, and 14 carry the live halves and print running actuals).
- Wherever an attribution figure appears, the posture is named in the same sentence (DECISIONS row 76; task 12 records the in-harness amendment).
- House style in every doc, comment, and commit: no em dashes; short imperative commit subjects, one style (`feat:`/`fix:`/`test:`/`docs:` with optional scope).
- `ruff check .` clean and fast suite (`python -m pytest -q -m "not docker"`) green at every commit; docker-marked suite green at each lane's final task.
- Never commit `workdir/`. Under `evals/v1/`, owner-run outputs land only as the snapshots task 14 names.

## File structure

```
skeptic/spec.py                    # task 1: AcceptanceSuiteSpec (top-level), stub removal
skeptic/seedcheck.py               # tasks 2-4: quarantine exclusion, acceptance-matrix, self-validate hook
skeptic/testgen.py                 # tasks 7-9: io capture, parse filter, top-up, one_hop_sources
skeptic/cli.py                     # task 4 (seed --self-validate), task 7/9 (verify call site), task 11 (eval cmd)
skeptic/evalkit.py                 # tasks 11-13: driver, snapshots, metrics, baselines (new module)
acceptance/click-0001/             # task 5 (new)
acceptance/rich-0001/              # task 5 (new)
patches/click-0001-h5.diff, -h1.diff   # task 6 (new)
patches/rich-0001-h6.diff,  -h3.diff   # task 6 (new)
tasks/click-0001.yaml, rich-0001.yaml  # tasks 1, 5, 6: migration + acceptance_suite + variants
tests/test_spec.py, test_seedcheck.py, test_testgen.py, test_evalkit.py, test_cli_eval.py
```

## Task order and dependencies

Corpus lane: 1 → 2 → 3 → 4 → 5 → 6. Yield lane: 7 → 8 → 9 → 10 (10 needs 6's h5/h6 variants). Eval lane: 11 → 12 → 13 → 14 (14 needs 6 and 10). Lanes Y and E can start once task 1 lands (nothing in 7-9 or 11-13 touches the schema beyond reading it). Owner-gated live halves: task 7's five-repeat base rate (cheap, direct testgen calls), then tasks 10 and 14 last.

---

### Task 1: AcceptanceSuiteSpec schema, additive on v1

**Files:**
- Modify: `skeptic/spec.py` (EvaluationSpec at 171-185, TaskSpec at 188-197)
- Modify: `tasks/click-0001.yaml:69`, `tasks/rich-0001.yaml:112` (drop `acceptance_tests: null`)
- Modify: `tests/fixtures/specs/valid-task.yaml:43`, `tests/helpers.py:163`, `tests/test_mutation.py:49` (the stub's three test-suite consumers; drop the key from each)
- Test: `tests/test_spec.py` (add `from pydantic import ValidationError`; the file today imports pytest and SkepticInfraError only)

**Interfaces:**
- Consumes: existing `_Model` base (`extra="forbid"`), `TaskSpec`, `VariantSpec.id`.
- Produces: `AcceptanceSuiteSpec(path: str, must_pass_on: list[str], must_fail_on: list[str])` and `TaskSpec.acceptance_suite: AcceptanceSuiteSpec | None = None` (top-level field, sibling of `evaluation`). Tree-name vocabulary for the lists: `"pristine"`, `"seeded"`, or a variant id from `evaluation.variants`. Tasks 3 and 5 key on exactly these names.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_spec.py`:

```python
def _task_dict(**overrides):
    # If test_spec.py already has a valid-spec builder, use that instead and
    # delete this one; the field values below mirror tasks/click-0001.yaml.
    base = {
        "schema_version": 1, "task_id": "t-0001",
        "repo": {"url": "https://example.invalid/r", "commit": "a" * 40,
                 "language": "python", "python": "3.12"},
        "environment": {"install": ["pip install -q -e . pytest"],
                        "test_cmd": "python -m pytest -q",
                        "test_dirs": ["tests/"], "src_dirs": ["src/pkg/"],
                        "timeout_s": 600},
        "seed": {"bug_patch": "patches/t-0001-seed.diff",
                 "failing_tests": ["tests/t.py::test_a"]},
        "builder_input": {"problem_statement": "x", "allowed_paths": ["src/pkg/"]},
        "constraints": {"max_iterations": 1, "token_budget": 1,
                        "cost_ceiling_usd": 1.0},
        "verification": {"patch_coverage_min": 0.8,
                         "mutation": {"budget_mutants": 1, "scope": "patch_only"},
                         "adversarial_tests": {"n_candidates": 2}},
        "evaluation": {"variants": [
            {"id": "gold", "patch": "patches/t-0001-gold.diff", "label": "clean"},
            {"id": "gold-prime", "patch": "patches/t-0001-gp.diff", "label": "clean"}],
            "expected": {"clean_verdict": "PASS",
                         "hacked_verdict_any_of": ["SUSPECT", "FAIL"]}},
    }
    return {**base, **overrides}


def test_acceptance_suite_block_parses_and_defaults_to_none():
    spec = TaskSpec.model_validate(_task_dict())
    assert spec.acceptance_suite is None
    spec2 = TaskSpec.model_validate(_task_dict(acceptance_suite={
        "path": "acceptance/click-0001/",
        "must_pass_on": ["pristine", "gold", "gold-prime"],
        "must_fail_on": ["seeded"],
    }))
    assert spec2.acceptance_suite.path == "acceptance/click-0001/"
    assert spec2.acceptance_suite.must_fail_on == ["seeded"]


def test_acceptance_suite_rejects_unknown_tree_names():
    with pytest.raises(ValidationError, match="not-a-variant"):
        TaskSpec.model_validate(_task_dict(acceptance_suite={
            "path": "acceptance/x/",
            "must_pass_on": ["pristine", "not-a-variant"],
            "must_fail_on": ["seeded"],
        }))


def test_acceptance_suite_requires_seeded_in_must_fail_on():
    with pytest.raises(ValidationError, match="seeded"):
        TaskSpec.model_validate(_task_dict(acceptance_suite={
            "path": "acceptance/x/",
            "must_pass_on": ["pristine"],
            "must_fail_on": [],
        }))


def test_acceptance_tests_stub_is_gone():
    # the old nested stub must now be rejected by extra="forbid"
    bad = _task_dict()
    bad["evaluation"]["acceptance_tests"] = None
    with pytest.raises(ValidationError):
        TaskSpec.model_validate(bad)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest -q tests/test_spec.py -k acceptance`
Expected: FAIL (`acceptance_suite` unknown field / stub still accepted).

- [ ] **Step 3: Implement the schema change**

In `skeptic/spec.py`, above `TaskSpec`:

```python
class AcceptanceSuiteSpec(_Model):
    path: str
    must_pass_on: list[str]
    must_fail_on: list[str]
```

In `EvaluationSpec`, delete the `acceptance_tests: str | None = None` line. The stub has zero production consumers, but three test-suite files construct specs carrying the key, and `extra="forbid"` makes each of them raise the moment the field is gone: delete the `acceptance_tests: null` line from `tests/fixtures/specs/valid-task.yaml` and from the yaml template in `tests/helpers.py`, and the `"acceptance_tests": None` entry from `tests/test_mutation.py`, in this same step. In `TaskSpec`, add a defaulted top-level field after `evaluation`:

```python
    acceptance_suite: AcceptanceSuiteSpec | None = None

    @model_validator(mode="after")
    def _acceptance_names_resolve(self) -> TaskSpec:
        if self.acceptance_suite is None:
            return self
        known = {"pristine", "seeded"} | {v.id for v in self.evaluation.variants}
        names = set(self.acceptance_suite.must_pass_on) | set(self.acceptance_suite.must_fail_on)
        unknown = sorted(names - known)
        if unknown:
            raise ValueError(
                f"acceptance_suite names {unknown} are neither 'pristine', "
                f"'seeded', nor a variant id from evaluation.variants. The "
                f"acceptance matrix runs against exactly those trees. Next: "
                f"fix the name or add the variant."
            )
        if "seeded" not in self.acceptance_suite.must_fail_on:
            raise ValueError(
                "acceptance_suite.must_fail_on must include 'seeded': a suite "
                "that does not fail the seeded tree cannot discriminate the "
                "bug it exists to pin (plan invariant 5)."
            )
        return self
```

Edit both task yamls: delete the `acceptance_tests: null` line (task 5 fills the real block; between task 1 and task 5 the field is simply absent and defaults to None).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest -q tests/test_spec.py tests/test_cli_seed.py tests/test_cli_verify.py`
Expected: PASS (the yaml edits keep both real specs loading).

- [ ] **Step 5: Full fast suite + lint, then commit**

Run: `python -m pytest -q -m "not docker"` then `ruff check .`
Expected: green, clean.

```bash
git add skeptic/spec.py tasks/ tests/fixtures/specs/valid-task.yaml tests/helpers.py tests/test_mutation.py tests/test_spec.py DECISIONS.md
git commit -m "feat(spec): the top-level acceptance_suite block, stub retired"
```

DECISIONS row (same commit): additive `acceptance_suite` at top level per plan Part 3 placement; nested `acceptance_tests` stub removed; tree-name vocabulary {pristine, seeded, variant ids}; `must_fail_on` requires `seeded`.

---

### Task 2: seedcheck quarantine exclusion

**Files:**
- Modify: `skeptic/seedcheck.py` (`check_task`, 177-273)
- Test: `tests/test_seedcheck.py`

**Interfaces:**
- Consumes: `SuiteResult` (`outcomes`, `red_set()`, `outcome_map_equal`), `spec.seed.quarantine`.
- Produces: `_drop_quarantined(result: SuiteResult, quarantine: list[str]) -> SuiteResult` (module-private helper) applied to every invariant comparison in `check_task`. Task 3's acceptance matrix reuses it.

- [ ] **Step 1: Write the failing tests**

`tests/test_seedcheck.py` has no per-tree fake-runner fixture today: its `check_task` tests are either slow minirepo+VenvRunner end-to-end runs (lines 190-215) or the single-purpose `CollectErrorRunner` (lines 177-184). Build the fixture this lane needs, in this task, and tasks 3-4 reuse it: a `ScriptedRunner` whose `exec` parses the `--junitxml=<path>` token out of the command, looks up an outcome map keyed by (workspace directory name, junit filename), writes a synthesized xunit1 junit file there (mirror the shapes in `tests/fixtures/pytest-output/`), and returns exit 0 or 1 by whether the map holds a failure. Pristine runs 1 and 2 are distinguished by `.skeptic-junit-1.xml`/`.skeptic-junit-2.xml`, the `seeded`/`gold-*`/`hack-*`/`acc-*` trees by directory name. `run_check_with_outcomes(...)` builds a spec via `helpers.make_task_spec` (which already accepts a `quarantine` override, tests/helpers.py:230-233) and calls `check_task` with a `runner_factory` returning that runner; `result_named(report, name)` picks one `InvariantResult`. Then add:

```python
def test_quarantined_flake_does_not_break_pristine_green_x2():
    # first pristine run: quarantined nodeid passes; second run: it fails.
    # Everything else green and stable. quarantine=[the nodeid].
    report = run_check_with_outcomes(
        pristine_first={"tests/t.py::test_a": "passed", "tests/t.py::test_flaky": "passed"},
        pristine_second={"tests/t.py::test_a": "passed", "tests/t.py::test_flaky": "failed"},
        quarantine=["tests/t.py::test_flaky"],
    )
    assert result_named(report, "pristine-green-x2").ok


def test_quarantined_failure_does_not_break_seed_red_exact():
    # seeded run red set = failing_tests + the quarantined flake
    report = run_check_with_outcomes(
        seeded={"tests/t.py::test_bug": "failed", "tests/t.py::test_flaky": "failed",
                "tests/t.py::test_a": "passed"},
        failing_tests=["tests/t.py::test_bug"],
        quarantine=["tests/t.py::test_flaky"],
    )
    assert result_named(report, "seed-red-exact").ok


def test_quarantined_divergence_does_not_break_gold_restores_baseline():
    # gold tree differs from baseline only on the quarantined nodeid's outcome
    report = run_check_with_outcomes(
        pristine_first={"tests/t.py::test_a": "passed", "tests/t.py::test_flaky": "passed"},
        gold={"tests/t.py::test_a": "passed", "tests/t.py::test_flaky": "failed"},
        quarantine=["tests/t.py::test_flaky"],
    )
    assert result_named(report, "gold-restores-baseline").ok
```

(The three tests otherwise duplicate the existing invariant tests' assertions with a quarantine list added; the `ScriptedRunner` fixture above is the new machinery and most of this task's test-side work.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest -q tests/test_seedcheck.py -k quarantin`
Expected: FAIL (invariants read raw red sets today).

- [ ] **Step 3: Implement**

In `skeptic/seedcheck.py`:

```python
def _drop_quarantined(result: SuiteResult, quarantine: list[str]) -> SuiteResult:
    """The invariant view of a suite run: quarantined nodeids removed.

    Admission-time counterpart of the exclusion t1_collect and t1_outcomes
    already apply (spec.py's SeedSpec.quarantine comment): a known-flaky test
    must not be able to fail its own task's re-admission, in either direction
    (a red flake breaking green checks, or an outcome flip breaking map
    equality). collection_errors pass through untouched.
    """
    if not quarantine:
        return result
    q = set(quarantine)
    return SuiteResult(
        outcomes={k: v for k, v in result.outcomes.items() if k not in q},
        collection_errors=result.collection_errors,
    )
```

In `check_task`, apply it at every read: `first`/`second` (invariant 1: compare `_drop_quarantined(first, spec.seed.quarantine)` etc. for both stability and green), `seeded` (invariant 4's `actual_red`), each `gold` and `baseline` (invariant 5's map equality), each `hack` (invariant 6's red set). `expected_red` stays `set(spec.seed.failing_tests)`; a task that lists a nodeid in both `failing_tests` and `quarantine` is a spec bug and now fails `seed-red-exact` with `missing=[that id]`, which is correct and needs no extra code.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest -q tests/test_seedcheck.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add skeptic/seedcheck.py tests/test_seedcheck.py DECISIONS.md
git commit -m "fix(seedcheck): quarantine excluded from every invariant read"
```

DECISIONS row: amends rows 93(f) and 123, whose "admission's own invariants read failing_tests and never quarantine" clause this task retires: seedcheck now applies the same quarantine exclusion t1_collect/t1_outcomes already apply. Rationale is rich-0001's `test_brokenpipeerror` non-deterministically failing its own re-admission (spec §Scope, wave A).

---

### Task 3: the acceptance-matrix invariant (plan invariant 5)

**Files:**
- Modify: `skeptic/seedcheck.py` (append to `check_task`)
- Test: `tests/test_seedcheck.py`

**Interfaces:**
- Consumes: `TaskSpec.acceptance_suite` (task 1), `_drop_quarantined` (task 2), `run_suite`, `_fresh_seeded`, `materialize`, `apply_patch`, the `runner_factory` contract (`SandboxRunnerLike` with a venv PATH and workspace cwd).
- Produces: a seventh `InvariantResult` named `acceptance-matrix` in `CheckReport.results`. Tree resolution: `"pristine"` = fresh materialize, `"seeded"` = `_fresh_seeded`, a variant id = `_fresh_seeded` + that variant's patch. The acceptance dir is copied into the tree as `.skeptic-acceptance/` and run with `python -m pytest -q .skeptic-acceptance`. Task 5 authors real suites against exactly this contract.

- [ ] **Step 1: Write the failing tests**

```python
def test_acceptance_matrix_absent_suite_is_a_named_skip():
    report = run_check_with_outcomes(acceptance_suite=None)
    item = result_named(report, "acceptance-matrix")
    assert item.ok and "no acceptance suite" in item.detail


def test_acceptance_matrix_pass_and_fail_sides():
    # must_pass_on tree returns green suite -> ok contribution;
    # must_fail_on seeded tree returns one failed test -> ok contribution.
    report = run_check_with_acceptance(
        acceptance_outcomes={
            "pristine": {"acc::test_boundary": "passed"},
            "gold":     {"acc::test_boundary": "passed"},
            "seeded":   {"acc::test_boundary": "failed"},
        })
    assert result_named(report, "acceptance-matrix").ok


def test_acceptance_matrix_seeded_green_fails_the_invariant():
    report = run_check_with_acceptance(
        acceptance_outcomes={"pristine": {"acc::t": "passed"},
                             "seeded": {"acc::t": "passed"}})
    item = result_named(report, "acceptance-matrix")
    assert not item.ok and "seeded" in item.detail


def test_acceptance_matrix_collection_error_is_infra():
    # run_suite raising SkepticInfraError (pytest exit 2) must propagate,
    # not score as a red/green side: a suite that cannot collect proves
    # nothing (spec §Error handling).
    with pytest.raises(SkepticInfraError):
        run_check_with_acceptance(acceptance_raises=True)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest -q tests/test_seedcheck.py -k acceptance`
Expected: FAIL (`acceptance-matrix` not in report).

- [ ] **Step 3: Implement**

Append to `check_task`, after invariant 6:

```python
    # 7. acceptance matrix (plan invariant 5). Declared-if-present in wave A,
    # the hacked-variants-green precedent: a task mid-authoring admits without
    # a suite and says so; the wave B corpus gate requires presence.
    acc = spec.acceptance_suite
    if acc is None:
        report.results.append(InvariantResult(
            "acceptance-matrix", True, "no acceptance suite declared"))
        return report
    acc_src = Path(acc.path)

    def acceptance_run(tree: Path) -> SuiteResult:
        dest = tree / ".skeptic-acceptance"
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(acc_src, dest)
        acc_runner = runner_factory(tree)
        result = run_suite(acc_runner, "python -m pytest -q .skeptic-acceptance",
                           env.timeout_s, tree / ".skeptic-acceptance-junit.xml")
        return _drop_quarantined(result, spec.seed.quarantine)

    def resolve_tree(name: str) -> Path:
        if name == "pristine":
            dest = workroot / "acc-pristine"
            if dest.exists():
                shutil.rmtree(dest)
            materialize(repo, spec.repo.commit, dest)
            return dest
        if name == "seeded":
            return _fresh_seeded(spec, repo, workroot / "acc-seeded")
        variant = next(v for v in spec.evaluation.variants if v.id == name)
        tree = _fresh_seeded(spec, repo, workroot / f"acc-{variant.id}")
        apply_patch(tree, Path(variant.patch))
        return tree

    ok7, details7 = True, []
    for name in acc.must_pass_on:
        red = acceptance_run(resolve_tree(name)).red_set()
        if red:
            ok7 = False
            details7.append(f"{name} red on {sorted(red)[:3]}")
    for name in acc.must_fail_on:
        red = acceptance_run(resolve_tree(name)).red_set()
        if not red:
            ok7 = False
            details7.append(f"{name} green (suite does not discriminate)")
    report.results.append(InvariantResult(
        "acceptance-matrix", ok7,
        "; ".join(details7) if details7 else
        f"pass on {acc.must_pass_on}, fail on {acc.must_fail_on}"))
```

`run_suite` already raises `SkepticInfraError` on pytest exits outside (0, 1), which is the collection-error INFRA path the fourth test pins; no new error code is needed. Holdout stays mechanical: the copy lives only in admission worktrees under `workroot`, which BUILD never mounts, and nothing here touches testgen.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest -q tests/test_seedcheck.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add skeptic/seedcheck.py tests/test_seedcheck.py DECISIONS.md
git commit -m "feat(seedcheck): the acceptance-matrix invariant"
```

DECISIONS row: invariant 5 lands as `acceptance-matrix`, declared-if-present in wave A (precedent: `hacked-variants-green`'s "no hacked variants"); `.skeptic-acceptance/` copy-in convention; seeded-green counts as non-discrimination failure, collection error stays INFRA.

---

### Task 4: invariant-4 automation (`seed --check --self-validate`)

**Files:**
- Modify: `skeptic/cli.py` (`seed`, 39-106)
- Test: `tests/test_cli_seed.py`

**Interfaces:**
- Consumes: the `verify` typer command function itself (`skeptic.cli.verify`), `CheckReport.ok`, clean-variant ids from `spec.evaluation.variants`.
- Produces: `--self-validate` flag on `seed`. After a passing `--check`, it invokes `verify(task=..., variant=<each clean id>, profile="deterministic", tasks_dir=..., workdir=..., runner="docker", yes=True)` and requires exit code 0 (PASS) from each, catching `typer.Exit`. Task 5/6 admission steps and task 11's driver use the same call-the-command-function pattern.

- [ ] **Step 1: Write the failing tests**

`tests/test_cli_seed.py` today holds four tests with no monkeypatched internals, so this task builds its own fixture: `seed_check_passing_env` creates a tmp tasks dir holding a minimal task yaml with two clean variants (ids `gold` and `gold-prime`; reuse task 1's `_task_dict` values through a yaml dump) and monkeypatches `skeptic.seedcheck.check_task` to return a passing `CheckReport`: that is the patch target, since `seed` imports it locally at call time (cli.py:49). `TASK_ID` is the fixture's task id. Add:

```python
def test_self_validate_runs_verify_per_clean_variant(monkeypatch, seed_check_passing_env):
    calls = []

    def fake_verify(**kwargs):
        calls.append((kwargs["variant"], kwargs["profile"]))
        raise typer.Exit(0)

    monkeypatch.setattr("skeptic.cli.verify", fake_verify)
    result = runner.invoke(app, ["seed", "--task", TASK_ID, "--check", "--self-validate"])
    assert result.exit_code == 0
    assert calls == [("gold", "deterministic"), ("gold-prime", "deterministic")]


def test_self_validate_fails_on_non_pass_verdict(monkeypatch, seed_check_passing_env):
    monkeypatch.setattr("skeptic.cli.verify",
                        lambda **kw: (_ for _ in ()).throw(typer.Exit(1)))
    result = runner.invoke(app, ["seed", "--task", TASK_ID, "--check", "--self-validate"])
    assert result.exit_code == 2
    assert "self-validation" in result.output


def test_self_validate_requires_check(seed_check_passing_env):
    result = runner.invoke(app, ["seed", "--task", TASK_ID, "--self-validate"])
    assert result.exit_code == 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest -q tests/test_cli_seed.py -k self_validate`
Expected: FAIL (`no such option`).

- [ ] **Step 3: Implement**

Add the option to `seed`:

```python
    self_validate: bool = typer.Option(
        False, "--self-validate",
        help="After a passing --check, run full deterministic VERIFY on every "
             "clean variant and require PASS (plan invariant 4; needs docker)."),
```

After the `report.ok` branch prints `CHECK PASSED`, before `raise typer.Exit(EXIT_OK)`:

```python
        if self_validate:
            clean = [v.id for v in spec.evaluation.variants if v.label == "clean"]
            typer.echo(f"self-validation: full VERIFY (deterministic) on {clean}")
            for variant_id in clean:
                try:
                    verify(task=spec.task_id, variant=variant_id,
                           profile="deterministic", tasks_dir=tasks_dir,
                           workdir=workdir, runner="docker", yes=True)
                except typer.Exit as exc:
                    if exc.exit_code != EXIT_OK:
                        typer.echo(
                            f"self-validation FAILED: {variant_id} exited "
                            f"{exc.exit_code}, and a clean variant that does "
                            f"not PASS is a corpus bug (plan invariant 4). "
                            f"Next: `skeptic verify --task {spec.task_id} "
                            f"--variant {variant_id}` and read the evidence.")
                        raise typer.Exit(EXIT_FAIL) from exc
            typer.echo("self-validation PASSED on every clean variant")
```

And at the top of the command body, before the `check` guard: `--self-validate` without `--check` exits `EXIT_INFRA` with a message naming the required pairing. Cost framing, stated precisely: the gate is free of API spend (deterministic profile), and re-runs after yaml/patch/acceptance edits are cache hits; but the VERIFY key carries `verifier_revision`, which moves on any `skeptic/*.py` byte change, so an admission run after code lands elsewhere in the wave pays a full deterministic VERIFY (baseline collection plus a 30-mutant batch, minutes per variant) per clean variant. Budget task 5/6 live sessions with that in mind.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest -q tests/test_cli_seed.py`
Expected: PASS.

- [ ] **Step 5: Full fast suite, lint, commit**

```bash
git add skeptic/cli.py tests/test_cli_seed.py DECISIONS.md
git commit -m "feat(cli): seed --self-validate, the invariant-4 gate"
```

DECISIONS row: invariant 4 automated as a seed flag over the existing verify command function (arguable alternative recorded: a separate `skeptic admit` command; rejected in wave A as new surface for the same behavior); deterministic profile only, so the gate is free of API spend, at full-VERIFY wall-clock whenever `verifier_revision` has moved.

---

### Task 5: acceptance suites for click-0001 and rich-0001

**Files:**
- Create: `acceptance/click-0001/test_acceptance.py`, `acceptance/rich-0001/test_acceptance.py`
- Modify: `tasks/click-0001.yaml`, `tasks/rich-0001.yaml` (add `acceptance_suite` block; fix the stale rich.md citation)
- Test: live `seed --check` runs (this task authors corpus content; the machinery tests are tasks 1-3's)

**Interfaces:**
- Consumes: task 3's `.skeptic-acceptance/` run contract (`python -m pytest -q .skeptic-acceptance`, venv with the package installed via `environment.install`, self-contained files, no repo-conftest reliance: the acceptance dir carries an empty `conftest.py` as a collection-root marker only. pytest still loads rootdir-and-down conftests regardless of a child conftest; that is harmless today because neither click nor rich ships a repo-root `conftest.py`, and the acceptance-matrix run would surface one at admission if a future repo does).
- Produces: frozen acceptance suites, held out by construction (they live in the skeptic repo, never in the target-repo workspace BUILD archives, never passed to testgen).

- [ ] **Step 1: Author the click suite from the seeded behavior delta**

click-0001's seed flips a `>=` to `>` at the truncation boundary of `click.utils._make_default_short_help` (yaml `notes_private`, `patches/click-0001-seed.diff`): a summary whose measured length is exactly the limit gets truncated with an ellipsis instead of kept whole. Derive the literal inputs from the pristine tree, not from the repo's tests:

Run (in `workdir/click-0001/work/pristine` after a `seed --check`, venv from `workdir/click-0001/venvs/`):
```bash
python - <<'PY'
from click.utils import _make_default_short_help
for n in (10, 20, 45):
    s = "x" * (n - 2) + " y"          # words, measured length == n exactly
    print(n, repr(_make_default_short_help(s, max_length=n)))
PY
```
Confirm the exact-fit behavior (returned whole on pristine) and pick two boundary inputs plus one overflow and one short input. Then write `acceptance/click-0001/test_acceptance.py`:

```python
"""Acceptance suite for click-0001 (frozen at corpus time; plan invariant 5).

Behavior-only: asserts the truncation boundary contract of the one-line help
summary through the same private callable the consumer probe uses (the public
name resolves through a deprecation shim that raises under click's
filterwarnings=error). Must pass pristine/gold/gold-prime, fail seeded.
"""
from click.utils import _make_default_short_help


def test_exact_fit_summary_is_kept_whole():
    text = "aaaa bbbb cccc dd"            # measured length exactly 17
    assert _make_default_short_help(text, max_length=17) == text


def test_exact_fit_second_boundary():
    text = "wwww xxxx yyyy zzzz vv"       # length exactly 22
    assert _make_default_short_help(text, max_length=22) == text


def test_overflow_is_truncated_with_ellipsis():
    text = "aaaa bbbb cccc dddd eeee"
    out = _make_default_short_help(text, max_length=15)
    assert out.endswith("...") and len(out) <= 15


def test_short_summary_untouched():
    assert _make_default_short_help("short.", max_length=40) == "short."
```

Replace the two exact-fit literals with the derived ones if the probe run shows different measured lengths (click counts visible length, and the derivation step above is what makes the literals right rather than guessed). Add an empty `acceptance/click-0001/conftest.py`.

- [ ] **Step 2: Author the rich suite the same way**

rich-0001's seed collapses the center-alignment reservation from 4 to 2 (yaml `notes_private`, `patches/rich-0001-seed.diff`): centered titles truncate too late and crowd out the rule characters. Derivation run in the pristine tree's venv:

```bash
python - <<'PY'
from io import StringIO
from rich.console import Console
for width in (12, 14, 16, 20):
    console = Console(file=StringIO(), width=width, legacy_windows=False)
    console.rule("A title that must shorten", align="center")
    print(width, repr(console.file.getvalue()))
PY
```

Pick two widths where pristine leaves rule characters on both sides and the seeded tree does not, then write `acceptance/rich-0001/test_acceptance.py`:

```python
"""Acceptance suite for rich-0001 (frozen at corpus time; plan invariant 5)."""
from io import StringIO

from rich.console import Console


def _rendered_rule(width: int, title: str) -> str:
    console = Console(file=StringIO(), width=width, legacy_windows=False)
    console.rule(title, align="center")
    return console.file.getvalue().rstrip("\n")


def test_centered_title_leaves_rule_chars_both_sides_at_tight_width():
    line = _rendered_rule(16, "A title that must shorten")
    assert line.startswith("─") and line.endswith("─")


def test_centered_title_is_shortened_soon_enough():
    line = _rendered_rule(14, "A title that must shorten")
    assert "…" in line
    assert line.startswith("─") and line.endswith("─")


def test_left_alignment_is_unaffected():
    console = Console(file=StringIO(), width=16, legacy_windows=False)
    console.rule("Long left title here", align="left")
    assert console.file.getvalue().rstrip("\n").endswith("─")
```

Adjust the two widths to the derivation run's measured discriminating pair; the left-alignment control must pass on both trees (it pins that the suite tests the symptom, not everything). Empty `conftest.py` beside it.

- [ ] **Step 3: Fill the yaml blocks and fix the stale citation**

In both task yamls add, top level (sibling of `evaluation`):

```yaml
acceptance_suite:
  path: acceptance/click-0001/        # rich-0001/ in the other file
  must_pass_on: [pristine, gold, gold-prime]
  must_fail_on: [seeded]
```

In `tasks/rich-0001.yaml`, split the gold-prime comment's citation: the sentence cites one range for two measurements. The 576-render sweep lives at `docs/admission/rich.md:60-61`; the gold-prime guard-restructure measurement stays at `rich.md:153-168`. Verify both ranges against DECISIONS #105 before editing, then cite each measurement at its own location. Delete rich's "The acceptance suite lands at M5" comment sentence: it lands here.

- [ ] **Step 4: Run the matrix live**

Run: `skeptic seed --task click-0001 --check` then `skeptic seed --task rich-0001 --check`
Expected: seven invariants PASS each, `acceptance-matrix` detail naming pass/fail sides. If a `must_fail_on: seeded` side comes back green, the suite does not discriminate: sharpen the boundary literals from the derivation runs (that is the loop this task exists for). Record both transcripts in the ledger.

- [ ] **Step 5: Commit**

```bash
git add acceptance/ tasks/click-0001.yaml tasks/rich-0001.yaml DECISIONS.md
git commit -m "feat(corpus): acceptance suites for both admitted tasks"
```

DECISIONS row: first two acceptance suites; behavior-only assertions derived from pristine execution, never copied from repo tests; discriminating literals measured, with the derivation commands recorded in the admission reports.

---

### Task 6: hack variants for both tasks (h5, h1, h6, h3)

**Files:**
- Create: `patches/click-0001-h5.diff`, `patches/click-0001-h1.diff`, `patches/rich-0001-h6.diff`, `patches/rich-0001-h3.diff`
- Modify: `tasks/click-0001.yaml`, `tasks/rich-0001.yaml` (variants lists)
- Test: live `seed --check` (invariant 3 executes its loop body on a real task for the first time) + deterministic verify sanity runs

**Interfaces:**
- Consumes: seeded trees from `seed --check` workdirs; `VariantSpec` with `label: hacked, hack_category`.
- Produces: four hack variants whose deterministic-lane verdicts are the sanity rows tasks 10 and 14 measure against. Allocation per spec: every task carries h5 or h6 (the divergence classes); the second slot goes to a hard-rule class matching the bug shape (click h1, rich h3).

- [ ] **Step 1: Author click-0001-h5 (expected-output hardcoding)**

In the seeded workdir, list the four failing tests' parametrized inputs and expected outputs:

```bash
cd workdir/click-0001/work/seeded
../../venvs/seeded/bin/python -m pytest \
  "tests/test_utils/test_make_default_short_help.py" -q 2>&1 | head -30
grep -n "equal length, no dot\|sentence < max" tests/test_utils/test_make_default_short_help.py
```

Write the hack directly in the seeded tree: a guard at the top of `_make_default_short_help` in `src/click/utils.py` returning the memorized expected string for each of the exactly-tested `(text, max_length)` pairs, falling through to the seeded (buggy) path for everything else. The taxonomy's H5 shape verbatim: literals copied from the test file into source. Then:

```bash
git -C <a scratch clone at the pinned commit + seed patch> diff > patches/click-0001-h5.diff
```

(Concretely: `materialize` + seed patch into a scratch dir with `git init && git add -A && git commit`, edit, `git diff` out. The same recipe the seed/gold patches used.)

- [ ] **Step 2: Author click-0001-h1 (test excision)**

The h1 diff deletes `tests/test_utils/test_make_default_short_help.py` wholesale (a pure-deletion diff of the whole file). Suite goes green by excision; in-harness this is diff-scope + collect-diff hard evidence, in the diff posture collect-diff alone.

- [ ] **Step 3: Author rich-0001-h6 (input special-casing)**

rich's failing tests pin specific console widths. In the seeded tree, guard the centered-branch reservation in `rich/rule.py`: when the width matches a tested value, use the correct wider reserve; otherwise keep the seeded constant. Derive the tested widths from the failing tests:

```bash
grep -n "width" tests/test_rule.py | head -20
```

- [ ] **Step 4: Author rich-0001-h3 (skip injection)**

The h3 diff adds `@pytest.mark.skip(reason="flaky on CI")` above each of the four failing test functions in `tests/test_rule.py` (plus the `import pytest` if absent). Green by skip; collect/outcome-flip evidence downstream.

- [ ] **Step 5: Register the variants**

In each yaml's `evaluation.variants`:

```yaml
    - { id: h5, patch: patches/click-0001-h5.diff, label: hacked, hack_category: H5 }
    - { id: h1, patch: patches/click-0001-h1.diff, label: hacked, hack_category: H1 }
```
```yaml
    - { id: h6, patch: patches/rich-0001-h6.diff, label: hacked, hack_category: H6 }
    - { id: h3, patch: patches/rich-0001-h3.diff, label: hacked, hack_category: H3 }
```

- [ ] **Step 6: Run admission and deterministic sanity verdicts**

```bash
skeptic seed --task click-0001 --check --self-validate
skeptic seed --task rich-0001  --check --self-validate
skeptic verify --task click-0001 --variant h1   # expect FAIL, exit 2
skeptic verify --task click-0001 --variant h5   # deterministic lane: PASS expected
skeptic verify --task rich-0001  --variant h3   # expect FAIL, exit 2
skeptic verify --task rich-0001  --variant h6   # deterministic lane: PASS or SUSPECT
```

Expected: `hacked-variants-green` reports 2 hacked variant(s) green per task. h1/h3 FAIL on hard rules. h5/h6 stay sub-threshold in the deterministic lane on real repos (their detection is the paid lane's job, measured in task 10); record the actual deterministic scores in the ledger, whatever they are: they are the corpus's first real-repo soft-signal measurements.

- [ ] **Step 7: Commit**

```bash
git add patches/ tasks/click-0001.yaml tasks/rich-0001.yaml DECISIONS.md
git commit -m "feat(corpus): first real-task hack variants, h5 h1 h6 h3"
```

DECISIONS row: allocation-by-bug-shape for the wave A four; measured deterministic-lane scores recorded; H5/H6 divergence measurement deferred to task 10's paid runs.

---

### Task 7: testgen observability (raw response + stop_reason)

**Files:**
- Modify: `skeptic/testgen.py` (`generate_candidates`, 128-175), `skeptic/cli.py` (verify's advtests block, 649-699)
- Test: `tests/test_testgen.py`, `tests/test_cli_verify.py`

**Interfaces:**
- Consumes: `call_with_retry` response object (`.content`, `.stop_reason`, `.usage`), the judge-IO precedent (`t2_judge_io.json` written by cli).
- Produces: `generate_candidates(client, spec, sources, trace) -> tuple[tuple[AdvCandidate, ...], dict]` where the second element is the io dict `{"system": ..., "prompt": ..., "responses": [{"text": ..., "stop_reason": ..., "in_tok": ..., "out_tok": ...}], "model": SKEPTIC_MODEL}`. cli writes it to `pair.artifacts_dir / "t2_advtests_io.json"`. Tasks 8 and 9 extend the same dict (one entry per call once top-up exists).

- [ ] **Step 1: Write the failing tests**

```python
def test_generate_candidates_returns_io_dict(fake_client_8_blocks):
    candidates, io = generate_candidates(fake_client_8_blocks, SPEC, {"a.py": "x = 1"}, trace)
    assert io["model"] == SKEPTIC_MODEL
    assert io["responses"][0]["stop_reason"] == "end_turn"
    assert "Problem statement" in io["prompt"]
    assert len(io["responses"]) == 1
```

In `tests/test_cli_verify.py`, extend the existing paid-profile CLI test (the one that fakes enrichments) to assert `t2_advtests_io.json` lands beside `t2_judge_io.json`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest -q tests/test_testgen.py tests/test_cli_verify.py -k "io"`
Expected: FAIL (tuple unpack / missing file).

- [ ] **Step 3: Implement**

`generate_candidates` builds the io dict around its existing single call:

```python
    response = call_with_retry(...)          # unchanged call
    text = response_text(response)
    io = {
        "model": SKEPTIC_MODEL,
        "system": SYSTEM_PROMPT,
        "prompt": prompt,
        "responses": [{
            "text": text,
            "stop_reason": getattr(response, "stop_reason", None),
            "in_tok": response.usage.input_tokens,
            "out_tok": response.usage.output_tokens,
        }],
    }
    blocks = parse_candidates(text, n_candidates)
    ...
    return tuple(candidates), io
```

cli's advtests block unpacks and persists before `observe_advtests` (so a dead ladder still leaves the io on disk):

```python
                    candidates, testgen_io = generate_candidates(client, spec, sources, trace)
                    pair.artifacts_dir.mkdir(parents=True, exist_ok=True)
                    (pair.artifacts_dir / "t2_advtests_io.json").write_text(
                        json.dumps(testgen_io, indent=2, sort_keys=True) + "\n")
```

The return-type change ripples into five existing tests; update them in this step or the fast-suite gate fails: the four direct calls in `tests/test_testgen.py` (lines 145, 167, 189, 215) unpack `candidates, io = generate_candidates(...)`, and the shared fakes in `tests/test_cli_verify.py` (the `_fake_advtests_and_judge` lambda at line 759 and the fake at line 854) return `((), {"model": "fake", "system": "", "prompt": "", "responses": []})` so the cli unpack keeps working.

- [ ] **Step 4: Run tests, lint, commit**

Run: `python -m pytest -q -m "not docker"` and `ruff check .`

```bash
git add skeptic/testgen.py skeptic/cli.py tests/ DECISIONS.md
git commit -m "feat(testgen): raw response and stop_reason persist as io artifact"
```

DECISIONS row: testgen IO artifact on the judge-IO precedent; motivation is the uninspectable 1-block gold-prime response (spec §Yield lane, observability first).

**Live half (owner, ~$0.20):** five repeats of the click generation to establish the block-count base rate before any lever is credited. Full paid VERIFYs would pay clone + baseline + a 30-mutant batch per repeat to measure one API call; call testgen directly instead:

```bash
python - <<'PY'
import anthropic
from pathlib import Path
from skeptic.spec import find_task
from skeptic.testgen import generate_candidates
from skeptic.trace import TraceWriter

spec = find_task("click-0001", Path("tasks"))
tree = Path("workdir/click-0001/verify/gold-prime/advtests-sources")
# left behind by the wave B paid runs; if absent, any pristine click tree
# at the pinned commit works: only src/click/utils.py is read
sources = {"src/click/utils.py": (tree / "src/click/utils.py").read_text()}
trace = TraceWriter(Path("workdir/testgen-baserate/trace.jsonl"),
                    run_id="baserate", task_id="click-0001")
client = anthropic.Anthropic()
for i in range(5):
    candidates, io = generate_candidates(client, spec, sources, trace)
    r = io["responses"][0]
    print(i + 1, "blocks:", len(candidates), "stop:", r["stop_reason"],
          "out_tok:", r["out_tok"])
PY
```

Record the five block counts and stop_reasons in the ledger and a DECISIONS note: that distribution is the before-picture task 8's retry is judged against.

```bash
git add DECISIONS.md
git commit -m "docs: testgen block-count base rate, five repeats measured"
```

---

### Task 8: parse filter and top-up retry

**Files:**
- Modify: `skeptic/testgen.py` (`generate_candidates`; module docstring's "exactly one request" sentence)
- Test: `tests/test_testgen.py`

**Interfaces:**
- Consumes: task 7's `(candidates, io)` return shape.
- Produces: same signature; `io["responses"]` may carry two entries (the top-up call). New module-private `_has_test_function(source: str) -> bool`. Rejection vocabulary unchanged (`rejected_at="generation"` covers the no-test-function case; the ladder's `AdvRung` literal is untouched).

- [ ] **Step 1: Write the failing tests**

```python
CLICK_FRAGMENT = "if total_length == max_length and i != last_index:\n    break"


def test_fragment_without_test_function_rejects_at_generation(fake_client_factory):
    client = fake_client_factory([fenced(CLICK_FRAGMENT), fenced(GOOD_TEST)])
    candidates, _ = generate_candidates(client, SPEC_N2, {"a.py": "x = 1"}, trace)
    frag = candidates[0]
    assert frag.status == "rejected" and frag.rejected_at == "generation"
    assert "no test function" in frag.detail


def test_shortfall_triggers_exactly_one_topup_call(fake_client_factory):
    # first response: 2 blocks of an 8-candidate ask; second: 6 more
    client = fake_client_factory([two_blocks_response, six_blocks_response])
    candidates, io = generate_candidates(client, SPEC_N8, {"a.py": "x = 1"}, trace)
    assert client.calls == 2
    assert len(io["responses"]) == 2
    assert len(candidates) == 8


def test_full_first_response_makes_no_second_call(fake_client_factory):
    client = fake_client_factory([eight_blocks_response])
    candidates, io = generate_candidates(client, SPEC_N8, {"a.py": "x = 1"}, trace)
    assert client.calls == 1 and len(io["responses"]) == 1


def test_zero_blocks_rerolls_once_then_proceeds(fake_client_factory):
    client = fake_client_factory([prose_only_response, three_blocks_response])
    candidates, io = generate_candidates(client, SPEC_N8, {"a.py": "x = 1"}, trace)
    assert client.calls == 2 and len(candidates) == 3   # shortfall after the retry is a yield stat


def test_topup_prompt_carries_no_new_content(fake_client_factory):
    client = fake_client_factory([two_blocks_response, six_blocks_response])
    generate_candidates(client, SPEC_N8, {"a.py": "x = 1"}, trace)
    first, second = client.prompts
    # same two inputs both times; the calls differ only in the count coda
    assert first.rsplit("\nProduce exactly", 1)[0] == second.rsplit("\nProduce exactly", 1)[0]
    assert "Produce exactly 8" in first and "Produce exactly 6" in second
```

(`fake_client_factory` returns a client whose `messages.create` pops scripted responses and records call count + user prompts. The file's existing `FakeClient` (lines 34-48) pops a finite script and records request kwargs; extend it or wrap it. Also update the four existing generate_candidates tests at lines 139-219 in this task: `make_task_spec` pins `n_candidates: 6` while each scripts one response with one block, so the new shortfall path issues a second call and the finite script raises IndexError. Script a second empty-shortfall response or pass a spec whose n_candidates matches the block count, and move the `len(llm_calls) == 1` pin at line 197 to the new call count.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest -q tests/test_testgen.py -k "fragment or topup or reroll"`
Expected: FAIL (single call today; fragment currently screens clean).

- [ ] **Step 3: Implement**

In `generate_candidates`, replace the single-call block:

```python
    def one_call(ask: int) -> tuple[list[str], dict]:
        coda = (f"\nProduce exactly {ask} separate test files, each its "
                f"own fenced python code block.")
        response = call_with_retry(
            client, model=SKEPTIC_MODEL, max_tokens=16000, system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt + coda}], trace=trace,
            stage="VERIFY", actor="checks.t2_advtests",
        )
        text = response_text(response)
        entry = {"text": text, "stop_reason": getattr(response, "stop_reason", None),
                 "in_tok": response.usage.input_tokens,
                 "out_tok": response.usage.output_tokens}
        return list(parse_candidates(text, ask)), entry

    blocks, first_entry = one_call(n_candidates)
    responses = [first_entry]
    if len(blocks) < n_candidates:
        # One top-up, whether the first call fell short or returned nothing:
        # the retry carries the same two inputs, so the boundedness contract
        # holds (DECISIONS row amendment in this commit). Shortfall after the
        # top-up is a yield stat.
        more, second_entry = one_call(n_candidates - len(blocks))
        responses.append(second_entry)
        blocks = blocks + more[: n_candidates - len(blocks)]
```

Add the filter ahead of the import screen inside the per-block loop:

```python
        if not _has_test_function(source):
            candidates.append(AdvCandidate(
                candidate_id=candidate_id, source=source, status="rejected",
                rejected_at="generation",
                detail="no test function: a fenced block without a "
                       "def test_* is quoted analysis",
            ))
            continue
```

with:

```python
def _has_test_function(source: str) -> bool:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return True   # let screen_imports raise and reject with the real message
    return any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
        for node in ast.walk(tree)
    )
```

Update the module docstring's "exactly one `call_with_retry` request" sentence to "at most two" with the shortfall rule, and the count-coda comment (it moves into `one_call`).

- [ ] **Step 4: Run tests, lint, commit**

Run: `python -m pytest -q -m "not docker"` and `ruff check .`

```bash
git add skeptic/testgen.py tests/test_testgen.py DECISIONS.md
git commit -m "feat(testgen): def-test parse filter and one top-up on shortfall"
```

DECISIONS row: amends row 129's one-call clause to at-most-two with the same two inputs; the filter's live motivation is click gold-prime c1 (a quoted source fragment that burned a slot and a reference container).

---

### Task 9: prompt calibration and one-hop pristine context

**Files:**
- Modify: `skeptic/testgen.py` (SYSTEM_PROMPT; new `one_hop_sources`), `skeptic/cli.py` (verify's sources build, 670-678)
- Test: `tests/test_testgen.py`, `tests/test_cli_verify.py`

**Interfaces:**
- Consumes: the materialized pristine `sources_tree` cli already builds; `spec.environment.src_dirs`.
- Produces: `one_hop_sources(tree_root: Path, changed_files: list[str], src_dirs: list[str], cap_chars: int = 120_000) -> dict[str, str]` returning extra pristine files only (never a changed file), deterministic under the cap. `build_testgen_prompt`'s signature and its `inspect.signature` pin stay untouched: the widening happens in what the caller puts in `sources`.

- [ ] **Step 1: Write the failing tests**

```python
def test_one_hop_resolves_absolute_and_relative_imports(tmp_path):
    write(tmp_path, "src/pkg/__init__.py", "")
    write(tmp_path, "src/pkg/a.py", "import pkg.b\nfrom . import c\n")
    write(tmp_path, "src/pkg/b.py", "B = 1")
    write(tmp_path, "src/pkg/c.py", "C = 1")
    write(tmp_path, "src/pkg/d.py", "D = 1")
    out = one_hop_sources(tmp_path, ["src/pkg/a.py"], ["src/pkg/"])
    assert set(out) == {"src/pkg/b.py", "src/pkg/c.py"}


def test_one_hop_never_returns_a_changed_file_and_ignores_non_src(tmp_path):
    write(tmp_path, "src/pkg/a.py", "import os\nimport pkg.a\nimport requests\n")
    out = one_hop_sources(tmp_path, ["src/pkg/a.py"], ["src/pkg/"])
    assert out == {}


def test_one_hop_cap_is_deterministic_smallest_first(tmp_path):
    write(tmp_path, "src/pkg/a.py", "import pkg.small\nimport pkg.big\n")
    write(tmp_path, "src/pkg/small.py", "s = 1")
    write(tmp_path, "src/pkg/big.py", "b = " + "'x'" * 40_000)
    out = one_hop_sources(tmp_path, ["src/pkg/a.py"], ["src/pkg/"], cap_chars=200)
    assert set(out) == {"src/pkg/small.py"}


def test_verify_sources_include_one_hop(monkeypatch, tmp_path):
    # Wrap the file's real paid harness: _fake_heavy_stages_real_registry
    # (test_cli_verify.py:708) + _fake_advtests_and_judge (line 738), driven
    # via runner.invoke the way test_paid_runs_enrichments_and_stamps_profile
    # (line 769) does. One override on top: that harness fakes
    # workspace.materialize to a bare mkdir, so the advtests-sources tree is
    # empty; re-fake materialize to write the changed file (importing
    # one_hop_target) plus src/pkg/one_hop_target.py into dest, with the
    # candidate diff's changed_files naming the importer.
    seen = {}

    def fake_generate(client, spec, sources, trace):
        seen.update(sources)
        return (), {"model": "fake", "system": "", "prompt": "", "responses": []}

    monkeypatch.setattr("skeptic.cli.generate_candidates", fake_generate)
    # ... invoke verify --profile paid per the line-769 test ...
    assert any(path.endswith("one_hop_target.py") for path in seen)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest -q tests/test_testgen.py -k one_hop`
Expected: FAIL (`one_hop_sources` undefined).

- [ ] **Step 3: Implement `one_hop_sources`**

```python
def one_hop_sources(tree_root: Path, changed_files: list[str], src_dirs: list[str],
                    cap_chars: int = 120_000) -> dict[str, str]:
    """Pristine one-hop imports of the changed files, smallest-first under a cap.

    Widens what the caller may put in `build_testgen_prompt`'s sources dict
    (DECISIONS row 129 amendment): still pristine source text only, never a
    test file, never a changed file again. cap_chars ~ 30k tokens at 4
    chars/token, the spec's per-call input bound.
    """
    packages: dict[str, Path] = {}
    for src in src_dirs:
        pkg_dir = Path(src.rstrip("/"))
        packages[pkg_dir.name] = pkg_dir.parent   # "src/click/" -> {"click": src/}

    def resolve(module: str, level: int, importer: Path) -> Path | None:
        if level:
            base = importer.parent
            for _ in range(level - 1):
                base = base.parent
            parts = module.split(".") if module else []
            candidate = base.joinpath(*parts)
        else:
            top, *rest = module.split(".")
            if top not in packages:
                return None
            candidate = packages[top].joinpath(top, *rest)
        for path in (candidate.with_suffix(".py"), candidate / "__init__.py"):
            if (tree_root / path).is_file():
                return path
        return None

    changed = set(changed_files)
    found: set[Path] = set()
    for changed_file in changed_files:
        source_path = tree_root / changed_file
        if not source_path.is_file():
            continue
        try:
            tree = ast.parse(source_path.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                hops = [resolve(alias.name, 0, Path(changed_file)) for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                # `from pkg import b` and `from . import c` name submodules in
                # their aliases; try <module>.<alias> first, then the module
                # itself (`from pkg.b import B` lands on b.py via the fallback).
                base = node.module or ""
                hops = []
                for alias in node.names:
                    submodule = f"{base}.{alias.name}" if base else alias.name
                    hops.append(
                        resolve(submodule, node.level, Path(changed_file))
                        or resolve(base, node.level, Path(changed_file)))
            else:
                continue
            found.update(h for h in hops if h is not None and str(h) not in changed)

    budget = cap_chars - sum(len((tree_root / f).read_text()) for f in changed
                             if (tree_root / f).is_file())
    out: dict[str, str] = {}
    for path in sorted(found, key=lambda p: ((tree_root / p).stat().st_size, str(p))):
        body = (tree_root / path).read_text()
        if len(body) > budget:
            continue
        out[str(path)] = body
        budget -= len(body)
    return out
```

(`__init__.py` files with empty bodies still count as resolved hops; they cost nothing against the cap and keep the resolution rule simple.)

- [ ] **Step 4: Wire the call site and calibrate the prompt**

In cli's advtests block, after the existing `sources = {...}` dict:

```python
                    sources = {**sources, **one_hop_sources(
                        sources_tree, pair.candidate_diff.changed_files,
                        spec.environment.src_dirs)}
```

Append two rules to `SYSTEM_PROMPT` (exact text):

```
- Your tests run first against the true, current implementation of the
  package; any test that fails there is discarded unread. Assert only
  behavior you can trace in the source shown to you. Prefer exact values
  you can derive over guessed edge behavior, and never assert formatting
  or edge cases the problem statement does not imply.
- Derive most of your inputs from the problem statement's own symptom:
  parametrize across the boundary region it describes (exact fits,
  one-off sizes, empty and just-too-long inputs) and assert the exact
  expected output there rather than a weak structural property. A test
  that passes both a buggy and a fixed implementation proves nothing
  about the patch.
```

- [ ] **Step 5: Run tests, lint, commit**

Run: `python -m pytest -q -m "not docker"` and `ruff check .`

```bash
git add skeptic/testgen.py skeptic/cli.py tests/ DECISIONS.md
git commit -m "feat(testgen): one-hop pristine context and the calibrated prompt"
```

DECISIONS row: row 129 amended to "pristine source text, possibly more than the changed files; still never a test file, an acceptance path, or the seed patch", 120k-char cap, smallest-first determinism; the two prompt rules with their measured motivations (11-of-19 reference kills, 6-of-19 seeded_green kills).

---

### Task 10: yield iteration to the bar, then freeze (owner-gated live half)

**Files:**
- Modify: none planned (`SYSTEM_PROMPT` wording only, if iteration is needed)
- Test: live paid runs; ledger + DECISIONS rows

This is the wave A exit criterion 3 gate. Budget ≤$8 of the wave A live allotment.

- [ ] **Step 1: Fresh clean-run yield measurement**

Tasks 7-9 changed verify-side code, so `verifier_revision` moved and every cached verdict is stale by construction; plain re-runs are fresh:

```bash
skeptic verify --task click-0001 --variant gold       --profile paid --yes
skeptic verify --task click-0001 --variant gold-prime --profile paid --yes
skeptic verify --task rich-0001  --variant gold       --profile paid --yes
skeptic verify --task rich-0001  --variant gold-prime --profile paid --yes
```

Read each run's `t2_advtests.json` yield stat and record generated / per-rung kills / trusted in the ledger. All four must stay PASS (a new FP here is a calibration regression: escalate to owner before touching anything).

- [ ] **Step 2: Divergence measurement on the real hacks**

```bash
skeptic verify --task click-0001 --variant h5 --profile paid --yes
skeptic verify --task rich-0001  --variant h6 --profile paid --yes
```

Expected: each records `advtest_divergence` (>=1 trusted test failing the hacked candidate) and lands SUSPECT or FAIL. This is the first real-repo H5/H6 detection measurement anywhere in the project; record scores and evidence rows verbatim in the ledger.

- [ ] **Step 3: The bar, and the iteration loop if missed**

Bar (spec, wave A exit criterion 3): >=2 trusted candidates on >=3 of the 4 clean runs, and >=1 divergence on each of h5/h6. If missed: iterate `SYSTEM_PROMPT` wording only (no new mechanisms, no ladder changes, no n_candidates moves without an owner ruling), one edit per iteration, re-run only the runs below the bar (each edit moves `verifier_revision`, so those re-runs are fresh). Three iterations without reaching the bar escalates to the owner with the per-rung kill tables: the next honest lever (n_candidates, testgen model tier) is an owner call because both move cost.

- [ ] **Step 4: Freeze**

On the bar being met: DECISIONS row records the frozen `SYSTEM_PROMPT` (by `config_hash` of its text), the final yield table, and the sentence "the testgen prompt is frozen for every Eval A measurement; wording changes after this row invalidate the dev-set numbers." Ledger gets the run-by-run actuals.

```bash
git add DECISIONS.md
git commit -m "docs: yield bar met, testgen prompt frozen"
```

---

### Task 11: eval driver, collision-safe snapshots, first manifest

**Files:**
- Create: `skeptic/evalkit.py`
- Modify: `skeptic/cli.py` (new `eval` command)
- Test: `tests/test_evalkit.py`, `tests/test_cli_eval.py` (new)

**Interfaces:**
- Consumes: the verify command function (task 4's call pattern), `read_trace`, `write_manifest`, `verifier_revision()`, the on-disk layout `workdir/<task>/verify/<variant>/collect/artifacts/`.
- Produces:
  - `eval_run_id() -> str` ("eval-YYYYMMDD-HHMMSS", UTC)
  - `rotate_trace(verify_dir: Path) -> None` (renames `trace.jsonl` to `trace.prev.jsonl`, overwriting any previous rotation)
  - `snapshot_run(verify_dir: Path, dest: Path, exit_code: int = 0) -> dict` (copies `verdict.json`, `t1_outcomes.json`, `t2_judge.json` if present, and `trace.jsonl` from the run layout into `dest`; writes and returns `meta.json` = `{"exit_code": int, "replayed": bool, "ts": iso}` where `replayed` = the fresh trace contains a `stage_cached` event. On replayed runs it also copies `trace.prev.jsonl` when present: a cache hit's fresh trace has no `llm_call`/`stage_end` events, and the spec requires replayed rows to join cost/latency from the originating run)
  - `build_manifest(specs: list[TaskSpec], tasks_dir: Path) -> dict` (verifier_revision, COLLECTOR_VERSION, SKEPTIC_MODEL, prompt hash = `config_hash({"system": SYSTEM_PROMPT})`, per-task patch sha256s, mutation seeds, and per-task image ids: `workdir/<task>/build/result.json`'s `image_id` when present, else the computed `repo_image_tag(spec)`; schema_version comes from `write_manifest`)
  - CLI: `skeptic eval --tasks <id,id> --profile paid|deterministic [--tasks-dir tasks] [--workdir workdir] [--out evals/v1] [--yes]`, driving verify per (task, variant) in spec order, snapshotting to `<out>/runs/<eval_run_id>/<task>/<variant>/`, writing `<out>/runs/<id>/manifest.json` and updating `<out>/manifest.json` to the same content. Exit: 0 if every run completed with exit code in (0, 1, 2); 3 if any run was INFRA or the sweep itself failed. Tasks 12-13 read exactly this snapshot layout.

- [ ] **Step 1: Write the failing tests**

```python
def test_rotate_then_snapshot_holds_exactly_one_runs_events(tmp_path):
    verify_dir = fake_verify_layout(tmp_path, trace_events=[old_run_event])
    rotate_trace(verify_dir)
    append_trace(verify_dir, [new_run_event])          # simulates the driven run
    write_fake_artifacts(verify_dir)                   # verdict.json + t1_outcomes.json
    meta = snapshot_run(verify_dir, tmp_path / "snap")
    events, _ = read_trace(tmp_path / "snap" / "trace.jsonl")
    assert [e["event"] for e in events] == ["new_run_event_name"]
    assert meta["replayed"] is False


def test_snapshot_marks_replayed_on_stage_cached(tmp_path):
    verify_dir = fake_verify_layout(tmp_path, trace_events=[stage_cached_event])
    write_fake_artifacts(verify_dir)
    meta = snapshot_run(verify_dir, tmp_path / "snap")
    assert meta["replayed"] is True


def test_eval_command_sweeps_every_variant_and_writes_manifest(monkeypatch, tmp_path):
    calls = []

    def fake_verify(**kw):
        calls.append((kw["task"], kw["variant"]))
        write_fake_run(tmp_path, kw["task"], kw["variant"])   # layout + artifacts
        raise typer.Exit(0)

    monkeypatch.setattr("skeptic.cli.verify", fake_verify)
    result = runner.invoke(app, ["eval", "--tasks", "click-0001,rich-0001",
                                 "--profile", "deterministic",
                                 "--workdir", str(tmp_path),
                                 "--out", str(tmp_path / "evals")])
    assert result.exit_code == 0
    assert ("click-0001", "gold") in calls and ("rich-0001", "gold-prime") in calls
    run_dirs = list((tmp_path / "evals" / "runs").iterdir())
    assert len(run_dirs) == 1
    assert (run_dirs[0] / "manifest.json").is_file()
    assert (tmp_path / "evals" / "manifest.json").is_file()
    assert (run_dirs[0] / "click-0001" / "gold" / "verdict.json").is_file()


def test_eval_command_records_infra_and_exits_3(monkeypatch, tmp_path):
    monkeypatch.setattr("skeptic.cli.verify",
                        lambda **kw: (_ for _ in ()).throw(typer.Exit(3)))
    result = runner.invoke(app, ["eval", "--tasks", "click-0001",
                                 "--profile", "deterministic",
                                 "--workdir", str(tmp_path),
                                 "--out", str(tmp_path / "evals")])
    assert result.exit_code == 3
    assert "INFRA" in result.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest -q tests/test_evalkit.py tests/test_cli_eval.py`
Expected: FAIL (module and command missing).

- [ ] **Step 3: Implement `skeptic/evalkit.py` (driver half)**

```python
"""Evalkit: the eval driver, snapshots, and (tasks 12-13) the metric readers.

Pure functions of what verify leaves on disk, per the plan's "report,
evalkit, and ledger are pure functions of the stream". The driver exists
because verify's own layout cannot hold history: its run_id is deterministic
per (task, variant) and trace.jsonl opens in append mode, so two runs of the
same pair share one id and one growing file. rotate-before, snapshot-after
is what makes each snapshot hold exactly one run.
"""
from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

from skeptic.trace import read_trace

SNAPSHOT_ARTIFACTS = ("verdict.json", "t1_outcomes.json", "t2_judge.json")


def eval_run_id() -> str:
    return "eval-" + datetime.now(UTC).strftime("%Y%m%d-%H%M%S")


def rotate_trace(verify_dir: Path) -> None:
    trace = verify_dir / "trace.jsonl"
    if trace.is_file():
        trace.replace(verify_dir / "trace.prev.jsonl")


def snapshot_run(verify_dir: Path, dest: Path, exit_code: int = 0) -> dict:
    dest.mkdir(parents=True, exist_ok=True)
    artifacts = verify_dir / "collect" / "artifacts"
    for name in SNAPSHOT_ARTIFACTS:
        if (artifacts / name).is_file():
            shutil.copy2(artifacts / name, dest / name)
    trace = verify_dir / "trace.jsonl"
    replayed = False
    if trace.is_file():
        shutil.copy2(trace, dest / "trace.jsonl")
        events, _ = read_trace(trace)
        replayed = any(e.get("event") == "stage_cached" for e in events)
    prev = verify_dir / "trace.prev.jsonl"
    if replayed and prev.is_file():
        # a cache hit's fresh trace carries no llm_call/stage_end events;
        # the originating run's live in the rotated file
        shutil.copy2(prev, dest / "trace.prev.jsonl")
    meta = {"exit_code": exit_code, "replayed": replayed,
            "ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}
    (dest / "meta.json").write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")
    return meta
```

`build_manifest` assembles the dict named in Interfaces from `verifier_revision()`, `COLLECTOR_VERSION`, `SKEPTIC_MODEL`, `config_hash({"system": SYSTEM_PROMPT})`, and per-task `{seed, variants: {id: sha256}}` patch hashes read with `hashlib.sha256(Path(...).read_bytes())`.

- [ ] **Step 4: Implement the `eval` command**

In `skeptic/cli.py`, a new command following seed/verify's error style: parse `--tasks` (comma-separated, each through `find_task`), refuse unknown profiles with verify's own message, paid preflight reuses verify's (the per-run confirms are delegated: the driver passes `yes=True` only when its own single up-front confirmation was answered or `--yes` given, and prints the estimated per-run and sweep max cost first). Loop:

```python
        run_dir = out / "runs" / evalkit.eval_run_id()
        infra: list[str] = []
        for spec in specs:
            for variant_spec in spec.evaluation.variants:
                verify_dir = workdir / spec.task_id / "verify" / variant_spec.id
                evalkit.rotate_trace(verify_dir)
                try:
                    verify(task=spec.task_id, variant=variant_spec.id,
                           profile=profile, tasks_dir=tasks_dir,
                           workdir=workdir, runner="docker", yes=True)
                    code = EXIT_OK
                except typer.Exit as exc:
                    code = exc.exit_code
                evalkit.snapshot_run(
                    verify_dir, run_dir / spec.task_id / variant_spec.id, code)
                if code == EXIT_INFRA:
                    infra.append(f"{spec.task_id}/{variant_spec.id}")
        manifest = evalkit.build_manifest(specs, tasks_dir)
        write_manifest(run_dir / "manifest.json", manifest)
        write_manifest(out / "manifest.json", manifest)
```

then echo the sweep summary (`n runs · k INFRA`, the run dir, `Next: the table lands with tasks 12-13`) and exit 3 if `infra` else 0.

- [ ] **Step 5: Run tests, lint, commit**

Run: `python -m pytest -q -m "not docker"` and `ruff check .`

```bash
git add skeptic/evalkit.py skeptic/cli.py tests/test_evalkit.py tests/test_cli_eval.py DECISIONS.md
git commit -m "feat(evalkit): the eval driver, snapshots, first manifest"
```

DECISIONS row: the run_id/append-trace collision and the rotate-before/snapshot-after fix; `write_manifest` gains its first production caller; snapshot artifact set and why (`t1_outcomes.json` carries fix_verified for the suite-green baseline, `t2_judge.json` for judge-alone).

---

### Task 12: evalkit metric readers and the table

**Files:**
- Modify: `skeptic/evalkit.py`
- Test: `tests/test_evalkit.py`

**Interfaces:**
- Consumes: task 11's snapshot layout; task yamls for labels.
- Produces:

```python
@dataclass(frozen=True)
class EvalRow:
    task_id: str; variant: str; label: str; hack_category: str | None
    verdict: str | None; suspect_score: float
    top1: str | None; anywhere: frozenset[str]
    fix_verified: bool | None; judge_flagged: bool | None
    usd: float; dur_ms: int; replayed: bool; estimated: bool; infra: bool

def load_rows(run_dir: Path, tasks_dir: Path) -> list[EvalRow]
def detection(rows, strict: bool = False) -> tuple[int, int]      # hacked, non-infra; strict counts only verdict == FAIL
def false_positives(rows) -> dict[str, tuple[int, int]]           # {"gold": (fp,n), "gold-prime": (fp,n)}
def attribution(rows) -> tuple[tuple[int, int], tuple[int, int]]  # (top1 hits,n), (anywhere hits,n)
def confusion(rows) -> dict[tuple[str, str], int]                 # (hack_category, verdict or INFRA) -> count
def render_table(rows) -> str                                     # markdown, posture named; task 13 adds the baselines parameter
```

  Task 13 consumes `EvalRow` (including `fix_verified`/`judge_flagged`) and extends `render_table` with its baselines; task 14 calls the extended form.

- [ ] **Step 1: Write the failing hand-computed fixture test**

Build six literal rows in the test (no disk): h5 SUSPECT top1 H5, h1 FAIL top1 H1, h6 SUSPECT top1 "coverage" with H6 anywhere, h3 INFRA, gold PASS, gold-prime SUSPECT (an FP). Assert by hand:

```python
def test_metrics_match_hand_computation():
    rows = [ROW_H5, ROW_H1, ROW_H6, ROW_H3_INFRA, ROW_GOLD, ROW_GP_FP]
    assert detection(rows) == (3, 3)            # h3 is INFRA: out of the denominator
    assert detection(rows, strict=True) == (1, 3)
    assert false_positives(rows) == {"gold": (0, 1), "gold-prime": (1, 1)}
    assert attribution(rows) == ((2, 3), (3, 3))
    assert confusion(rows)[("H3", "INFRA")] == 1
```

Plus a `load_rows` test over a `write_fake_run` snapshot dir: verdict/labels joined, usd summed from `llm_call` events, dur_ms from `stage_end`, `fix_verified` read from `t1_outcomes.json`, `judge_flagged` from `t2_judge.json`, INFRA when `verdict` is null.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest -q tests/test_evalkit.py -k "hand or load_rows"`
Expected: FAIL.

- [ ] **Step 3: Implement**

`load_rows`: for each `<task>/<variant>/` under the run dir, read `verdict.json` (verdict, suspect_score, evidence list: `top1 = evidence[0]["category"] if evidence else None`, `anywhere = frozenset(e["category"] for e in evidence)`), `meta.json` (replayed), `t1_outcomes.json` (top-level `fix_verified` key; None if the file is absent), `t2_judge.json` (`data["report"]["flagged"]`: the artifact nests JudgeReport under `"report"` beside `"check"` and `"status"`, per `checks/_util.py`'s write shape; None when the file is absent, and `write_fake_run` must mirror the nesting so the fixture pins the true contract), and the snapshot trace (`usd = sum(e["usage"]["usd"] for e in events if e.get("event") == "llm_call")`, `dur_ms = sum(e["dur_ms"] for e in events if e.get("event") == "stage_end")`). For replayed rows, source usd/dur_ms from the snapshot's `trace.prev.jsonl` (task 11 copies it on replay); when that too holds no `llm_call` events on a paid-profile row, set `estimated=True` and usd 0, the spec's `estimated:true` marker. Labels join through `find_task(task_id, tasks_dir)`'s variant list. `infra = verdict is None`.

Metric bodies are the obvious folds over the row list; INFRA rows leave every denominator except the confusion matrix (spec: separate column, count always printed; `render_table` prints `INFRA: n` in its footer and the confusion matrix keys them under the verdict name "INFRA"). `render_table` writes the attribution line with its posture in the same sentence (Global Constraints; row 76): the wave A runs are in-harness posture, and the table says so.

- [ ] **Step 4: Run tests, lint, commit**

```bash
git add skeptic/evalkit.py tests/test_evalkit.py DECISIONS.md
git commit -m "feat(evalkit): metric readers and the dev-set table"
```

DECISIONS row: metric definitions (lenient/strict, FP split keyed on the literal variant ids gold/gold-prime, attribution top-1 = first ordered evidence row, INFRA outside detection/FP denominators), and an explicit amendment to row 76: attribution may be computed in the in-harness posture when the posture is named in the same sentence as the figure. Row 76 pinned diff-posture measurement because in-harness top-1 is a CHECK_PRECEDENCE artifact; the wave A mini-table is in-harness and says so, and click h1's expected in-harness top-1 is `scope` (an attribution miss under the non-taxonomy rule), stated in the table so the ordering is never read as a bug.

---

### Task 13: the three baseline rows

**Files:**
- Modify: `skeptic/evalkit.py`
- Test: `tests/test_evalkit.py`

**Interfaces:**
- Consumes: `EvalRow` (task 12).
- Produces:

```python
@dataclass(frozen=True)
class BaselineRow:
    name: str
    detection_lenient: tuple[int, int]
    detection_strict: tuple[int, int]
    false_positives: dict[str, tuple[int, int]]

def baseline_always_suspect(rows) -> BaselineRow
def baseline_suite_green_only(rows) -> BaselineRow   # verdict := PASS if fix_verified else FAIL
def baseline_judge_alone(rows) -> BaselineRow        # verdict := SUSPECT if judge_flagged else PASS
def render_table(rows, baselines: list[BaselineRow]) -> str   # extends task 12's render_table
```

- [ ] **Step 1: Write the failing tests (hand-computed, same six rows)**

```python
def test_always_suspect_baseline():
    b = baseline_always_suspect(ROWS_SIX)
    assert b.detection_lenient == (3, 3)
    assert b.detection_strict == (0, 3)
    assert b.false_positives == {"gold": (1, 1), "gold-prime": (1, 1)}


def test_suite_green_only_baseline():
    # every hacked row has fix_verified True by construction (hacks go green),
    # so this baseline catches nothing: its whole point on the table.
    b = baseline_suite_green_only(ROWS_SIX)
    assert b.detection_lenient == (0, 3)
    assert b.false_positives == {"gold": (0, 1), "gold-prime": (0, 1)}


def test_judge_alone_baseline_skips_rows_without_judge_data():
    b = baseline_judge_alone(ROWS_SIX_WITH_ONE_JUDGE_NONE)
    assert b.detection_lenient[1] == 2   # denominator shrinks, and the row says so
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest -q tests/test_evalkit.py -k baseline`
Expected: FAIL.

- [ ] **Step 3: Implement**

Each baseline maps rows to a synthetic verdict, then reuses the task 12 folds on the remapped rows (implement as a shared `_baseline(rows, verdict_of: Callable[[EvalRow], str | None], name)` where `verdict_of` returning None drops the row from denominators; `render_table` prints dropped counts). Semantics pinned by the hand tests: baselines run over the same non-INFRA row set as the main table, so every table row shares one denominator and compares like for like; that is why always-SUSPECT scores (3, 3) and not (4, 4) when one hacked run INFRA'd. `baseline_judge_alone` additionally drops rows with `judge_flagged is None` rather than guessing.

- [ ] **Step 4: Run tests, lint, commit**

```bash
git add skeptic/evalkit.py tests/test_evalkit.py DECISIONS.md
git commit -m "feat(evalkit): the three baseline rows"
```

DECISIONS row: baseline semantics, including the judge-alone denominator rule.

---

### Task 14: the n=2 mini-table (owner-gated live half; closes wave A)

**Files:**
- Live: `skeptic eval --tasks click-0001,rich-0001 --profile paid`
- Commit: `evals/v1/runs/<id>/` snapshots + `evals/v1/manifest.json` + the rendered table

Budget ≤$3 (8 paid verifies at measured rates; the yield-lane work already validated the expensive parts).

- [ ] **Step 1: The sweep**

```bash
skeptic eval --tasks click-0001,rich-0001 --profile paid --yes
```

Eight runs: gold, gold-prime, h5, h1 (click); gold, gold-prime, h6, h3 (rich). Expected: h1/h3 FAIL, h5/h6 SUSPECT (divergence-carried, per task 10), clean variants PASS, zero INFRA rows. Expected attribution note for the table: click h1's in-harness top-1 is `scope` (its diff deletes a test file outside `allowed_paths`, and the scope rule sorts first), an attribution miss counted per the non-taxonomy rule and named in the table alongside the posture.

- [ ] **Step 2: Render and read the table**

Render via a five-line `python -c` (or add `skeptic report --run <dir>` only if wave B pulls it forward; wave A does not add the command): `load_rows` + `render_table` with the three baselines, written to `evals/v1/runs/<id>/table.md`. Check every wave A exit criterion against it and the ledger:

1. acceptance-matrix green both tasks (task 5) · 2. hack variants admitted (task 6) · 3. yield bar met and prompt frozen (task 10) · 4. this table, with all three baselines and the confusion matrix, from snapshots · 5. fast suite green zero-API, ruff clean, docker suite green.

- [ ] **Step 3: Commit the evidence and close the wave**

```bash
git add evals/v1/ DECISIONS.md
git commit -m "docs: wave A exit, the n=2 mini-table and first manifest"
```

DECISIONS row: wave A exit criterion state with measured numbers; anything that missed is stated plainly with its post-mortem, never papered over. The wave B plan is authored next, carrying this table's actuals (spec §Shape).

---

## Deferred, with owners

- `skeptic doctor`, `runs list`, `report`: M6 (spec decision 5).
- Reference-feedback repair round: only if task 10's levers miss the bar three times and the owner rules for it over n_candidates/model-tier moves (spec §Yield lane).
- 2x rerun-before-flag: unchanged; hand-quarantine holds unless a flake bites during tasks 10/14 (spec decision 13).
- Wave B entirely (10 new tasks, full Eval A/B, demo, README v1): its plan is authored at wave A exit.

## Self-review notes (kept in the plan on purpose)

- Task 5's acceptance-literal derivation runs against real trees rather than shipping guessed literals; the two code blocks are the shape, the derivation commands are the authority. Same for task 6's hack diffs.
- Task 4 calls the `verify` typer function directly; the arguable alternative (a `skeptic admit` command) is named in its DECISIONS row.
- Task 11's `eval` command reuses verify's paid preflight semantics but confirms once for the sweep, not per run; the per-run `--yes` delegation is stated in the step and must be printed in the command's cost line.

