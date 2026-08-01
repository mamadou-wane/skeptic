import hashlib
from pathlib import Path

import typer

import skeptic
from skeptic.errors import SkepticInfraError
from skeptic.spec import TaskSpec, VariantSpec

EXIT_OK = 0
EXIT_SUSPECT = 1
EXIT_FAIL = 2
EXIT_INFRA = 3

app = typer.Typer(no_args_is_help=True, add_completion=False)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"skeptic {skeptic.__version__}")
        raise typer.Exit(EXIT_OK)


@app.callback()
def main(
    version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True,
        help="Print version and exit.",
    ),
) -> None:
    """Skeptic: audits coding-agent patches for reward hacking."""


@app.command()
def seed(
    task: str = typer.Option(..., "--task", help="Task id (tasks/<id>.yaml)."),
    check: bool = typer.Option(False, "--check", help="Run corpus admission invariants."),
    tasks_dir: Path = typer.Option(Path("tasks"), "--tasks-dir"),  # noqa: B008
    workdir: Path = typer.Option(Path("workdir"), "--workdir"),  # noqa: B008
    runner: str = typer.Option("venv", "--runner", help="venv (verify-only) or docker."),
) -> None:
    """Apply a task's seed bug and (with --check) enforce admission invariants."""
    from skeptic.sandbox import VenvRunner
    from skeptic.seedcheck import check_task
    from skeptic.spec import find_task
    from skeptic.trace import TraceWriter, config_hash

    try:
        spec = find_task(task, tasks_dir)
        # Normalize the work dir to an absolute path up front: the runner puts
        # the venv bin dir on PATH and runs each command with cwd set to the
        # workspace, so a relative work dir would make those PATH/venv lookups
        # resolve against the wrong directory.
        workdir = workdir.resolve()
        task_workdir = workdir / spec.task_id
        trace = TraceWriter(task_workdir / "trace.jsonl",
                            run_id=f"seedcheck-{config_hash({'task': spec.task_id})}",
                            task_id=spec.task_id)
        trace.event(stage="LOAD", actor="orchestrator", event="spec_loaded")
        if not check:
            typer.echo(
                "seed without --check is not implemented yet (M2 wires the full "
                "SEED stage). Next: `skeptic seed --task <id> --check`."
            )
            raise typer.Exit(EXIT_INFRA)
        if runner != "venv":
            typer.echo(
                "Only --runner venv is wired in M1 (verify-only, reduced "
                "isolation). Docker runner lands with the BUILD stage."
            )
            raise typer.Exit(EXIT_INFRA)

        def runner_factory(workspace: Path) -> VenvRunner:
            venv_runner = VenvRunner(
                workspace=workspace,
                venv_dir=task_workdir / "venvs" / workspace.name,
            )
            venv_runner.setup(spec.environment.install)
            return venv_runner

        trace.event(stage="SEED", actor="orchestrator", event="check_start")
        report = check_task(
            spec,
            workroot=task_workdir / "work",
            runner_factory=runner_factory,
            repo_cache=task_workdir / "repo-cache",
        )
        for item in report.results:
            mark = "PASS" if item.ok else "FAIL"
            typer.echo(f"  {mark}  {item.name}: {item.detail}")
        trace.event(stage="SEED", actor="orchestrator", event="check_end",
                    payload={"ok": report.ok})
        if report.ok:
            typer.echo(f"CHECK PASSED — {spec.task_id} admitted to the corpus")
            raise typer.Exit(EXIT_OK)
        typer.echo(f"CHECK FAILED — fix the invariants above, then re-run "
                   f"`skeptic seed --task {spec.task_id} --check`")
        raise typer.Exit(EXIT_FAIL)
    except SkepticInfraError as exc:
        typer.echo(f"INFRA ERROR: {exc}")
        raise typer.Exit(EXIT_INFRA) from exc


def _docker_available() -> bool:
    from skeptic.sandbox import docker_available
    return docker_available()


def _baseline_payload(
    seed_red: list[str] | None, environmental_red: list[str] | None,
    total: int | None, collection_errors: int | None,
) -> dict:
    """One `baseline_suite` payload shape for the live run and the cache-hit
    replay. The values arrive as None when a cache entry predates the keys."""
    return {"seed_red": seed_red, "environmental_red": environmental_red,
            "total": total, "collection_errors": collection_errors}


def _build_cache_key(spec: TaskSpec, model: str, image_id: str, seed_hash: str) -> str:
    """BUILD stage cache key.

    Every input that shapes the Builder's run, or the first message it reads
    (problem_statement, hints, test_cmd), belongs here: a resumed run against
    an edited spec must get a different key, not silently replay the cached
    candidate for the old spec (2026-07-26 review finding 1). image_id does
    not stand in for repo.commit: the final image holds only the frozen
    dependency closure and no source, so a commit bump that doesn't touch
    environment.install can leave image_id unchanged.
    """
    from skeptic.builder import GREEN_RULE_VERSION, prompt_version
    from skeptic.trace import config_hash

    return config_hash({
        "stage": "BUILD", "task": spec.task_id, "seed": seed_hash,
        "model": model, "prompt": prompt_version(),
        "green_rule": GREEN_RULE_VERSION,
        "image": image_id,
        "commit": spec.repo.commit,
        "constraints": spec.constraints.model_dump(),
        "builder_input": spec.builder_input.model_dump(),
        "environment": spec.environment.model_dump(),
    })


@app.command()
def build(
    task: str = typer.Option(..., "--task"),
    model: str = typer.Option("claude-opus-5", "--model"),
    tasks_dir: Path = typer.Option(Path("tasks"), "--tasks-dir"),  # noqa: B008
    workdir: Path = typer.Option(Path("workdir"), "--workdir"),  # noqa: B008
    runner: str = typer.Option("docker", "--runner", help="docker; venv is refused."),
    yes: bool = typer.Option(False, "--yes", help="Skip the cost confirmation."),
) -> None:
    """Run the Builder against a task's seeded bug inside the tool-exec sandbox."""
    import json
    import os
    import shutil
    import time

    from skeptic.builder import GREEN_RULE_VERSION, PRICING, run_build
    from skeptic.builder_tools import ToolContext, run_baseline_suite
    from skeptic.candidate import extract_candidate, snapshot
    from skeptic.errors import VenvBuildRefused
    from skeptic.image import ensure_repo_image
    from skeptic.orchestrator import StageCache, run_stage
    from skeptic.sandbox import SessionContainer, VenvRunner
    from skeptic.spec import find_task
    from skeptic.trace import TraceWriter, config_hash
    from skeptic.workspace import apply_patch, clone_pinned, materialize

    try:
        spec = find_task(task, tasks_dir)
        if runner == "venv":
            VenvRunner(workspace=Path("."), venv_dir=Path(".")).build_stage_guard()
        if runner != "docker":
            typer.echo(f"Unknown runner {runner!r}: build runs in docker only.")
            raise typer.Exit(EXIT_INFRA)
        # key before any docker/image work (plan section 12): fail in one
        # second with the exact env var, before any 90-second image build
        if not os.environ.get("ANTHROPIC_API_KEY"):
            typer.echo(
                "ANTHROPIC_API_KEY is not set. The Builder loop calls the "
                "Anthropic API from the host (the key never enters the "
                "sandbox). Next: export ANTHROPIC_API_KEY and re-run."
            )
            raise typer.Exit(EXIT_INFRA)
        if model not in PRICING:
            typer.echo(
                f"No pricing entry for {model!r}; the cost ceiling cannot be "
                f"enforced. Next: add a sourced price to PRICING in "
                f"skeptic/builder.py."
            )
            raise typer.Exit(EXIT_INFRA)
        if not _docker_available():
            typer.echo(
                "Docker daemon unavailable. BUILD runs the Builder's tools in "
                "a hardened container; there is no reduced-isolation fallback "
                "for BUILD. Next: start Docker Desktop, then re-run."
            )
            raise typer.Exit(EXIT_INFRA)
        ceiling = spec.constraints.cost_ceiling_usd
        typer.echo(f"Builder run: task={spec.task_id} model={model} "
                   f"cost ceiling ${ceiling:.2f}")
        if not yes and not typer.confirm("Proceed (this spends real API money)?"):
            typer.echo(
                "Declined: the run was not started, so no API spend "
                "happened. Skeptic confirms before every billed Builder run "
                f"unless --yes is passed. Next: re-run `skeptic build --task "
                f"{spec.task_id} --yes` once you're ready to spend."
            )
            raise typer.Exit(EXIT_INFRA)

        workdir = workdir.resolve()
        build_dir = workdir / spec.task_id / "build"
        build_dir.mkdir(parents=True, exist_ok=True)
        repo = clone_pinned(spec.repo.url, spec.repo.commit,
                            workdir / spec.task_id / "repo-cache")

        # image first: its context is the pristine export, deleted right after
        pristine = build_dir / "image-context"
        if pristine.exists():
            shutil.rmtree(pristine)
        materialize(repo, spec.repo.commit, pristine)
        image = ensure_repo_image(spec, pristine, build_dir / "image")
        shutil.rmtree(pristine)

        seeded = build_dir / "workspace"
        baseline_tree = build_dir / "baseline"
        for stale in (seeded, baseline_tree):
            if stale.exists():
                shutil.rmtree(stale)
        materialize(repo, spec.repo.commit, seeded)
        apply_patch(seeded, Path(spec.seed.bug_patch))
        snapshot(seeded, baseline_tree)

        seed_hash = config_hash({"seed": Path(spec.seed.bug_patch).read_text()})
        cache_key = _build_cache_key(spec, model, image.image_id, seed_hash)
        trace = TraceWriter(build_dir / "trace.jsonl",
                            run_id=f"build-{cache_key}", task_id=spec.task_id)
        ro = tuple(spec.environment.test_dirs) \
            + tuple(spec.environment.config_files) \
            + tuple(spec.environment.golden_dirs)

        def do_build() -> dict:
            import anthropic
            client = anthropic.Anthropic()
            with SessionContainer(image.tag, seeded, ro_subpaths=ro) as session:
                # The baseline runs after the overlay install and before the
                # Builder's first tool call (row 74). Position here is the
                # only thing enforcing the second half.
                started = time.monotonic()
                baseline_suite = run_baseline_suite(seeded, session, spec)
                baseline_ms = int((time.monotonic() - started) * 1000)
                red = baseline_suite.red_set()
                # the red set alone goes in the trace: click-0001 runs the
                # 1940 tests row 73 measured, and read_trace loads the whole
                # trace file into memory
                seed_red = sorted(red & set(spec.seed.failing_tests))
                environmental_red = sorted(red - set(spec.seed.failing_tests))
                trace.event(stage="BUILD", actor="orchestrator",
                            event="baseline_suite", dur_ms=baseline_ms,
                            payload=_baseline_payload(
                                seed_red, environmental_red,
                                len(baseline_suite.outcomes),
                                baseline_suite.collection_errors))
                ctx = ToolContext(
                    workspace=seeded, session=session, spec=spec,
                    baseline_passed=frozenset(baseline_suite.passed_set()),
                    baseline_collection_errors=baseline_suite.collection_errors)
                result = run_build(spec, ctx, trace, model=model, client=client)
            report = extract_candidate(
                baseline_tree, seeded, build_dir / "candidate.diff",
                allowed_paths=spec.builder_input.allowed_paths)
            return {
                "stop_reason": result.stop_reason, "iterations": result.iterations,
                "in_tokens": result.in_tokens, "out_tokens": result.out_tokens,
                "usd": round(result.usd, 4), "green": result.green,
                "green_rule": GREEN_RULE_VERSION,
                # both red sets are sorted lists: StageCache.put json.dumps
                # this dict verbatim, and a set raises there
                "baseline_seed_red": seed_red,
                "baseline_environmental_red": environmental_red,
                "baseline_total": len(baseline_suite.outcomes),
                "baseline_collection_errors": baseline_suite.collection_errors,
                "candidate": str(report.diff_path),
                "changed_files": report.changed_files,
                "out_of_scope": report.out_of_scope,
                "is_empty": report.is_empty,
                "image_id": image.image_id,
            }

        cache = StageCache(build_dir / "cache")
        cache_hit = cache.get(cache_key) is not None
        outcome = run_stage(cache, "BUILD", cache_key, do_build, trace)
        if cache_hit:
            # Row 74 records the baseline red set in the trace on every run,
            # and a cache hit never executes do_build. Replay it from the
            # cached dict, tagged so a reader can tell the two apart.
            trace.event(stage="BUILD", actor="orchestrator",
                        event="baseline_suite",
                        payload={**_baseline_payload(
                            outcome.get("baseline_seed_red"),
                            outcome.get("baseline_environmental_red"),
                            outcome.get("baseline_total"),
                            outcome.get("baseline_collection_errors")),
                            "cached": True})
        (build_dir / "result.json").write_text(json.dumps(outcome, indent=2) + "\n")
        typer.echo(f"stop: {outcome['stop_reason']} · iterations: "
                   f"{outcome['iterations']} · green: "
                   f"{outcome.get('green')} · cost: ${outcome['usd']:.2f}")
        typer.echo(f"candidate: {outcome['candidate']}")
        if outcome["out_of_scope"]:
            typer.echo(f"out-of-scope edits (recorded for VERIFY): "
                       f"{outcome['out_of_scope']}")
        if outcome["is_empty"]:
            typer.echo("empty candidate: the Builder produced no patch "
                       "(FAIL(no-patch) per plan section 10)")
            raise typer.Exit(EXIT_FAIL)
        typer.echo("Next: `skeptic verify` lands at M3; the candidate and "
                   "trace are ready for it.")
        raise typer.Exit(EXIT_OK)
    except VenvBuildRefused as exc:
        typer.echo(f"REFUSED: {exc}")
        raise typer.Exit(EXIT_INFRA) from exc
    except SkepticInfraError as exc:
        typer.echo(f"INFRA ERROR: {exc}")
        raise typer.Exit(EXIT_INFRA) from exc


def _verify_cache_key(spec: TaskSpec, variant: VariantSpec) -> str:
    """VERIFY stage cache key.

    Every input that shapes an observation or a check belongs here: the
    variant patch bytes (so a byte-identical patch under a different path
    collides, and one edited byte does not), the whole seed sub-spec
    (`spec.seed.model_dump()`, with `bug_patch` swapped for its sha256 so the
    same bytes-not-path rule applies there too) since `t1_outcomes` reads
    `failing_tests`/`quarantine` directly for `fix_verified` and every
    flip/regression rule and `t1_collect` reads `quarantine` as well,
    `repo.commit` and `environment` (which together determine the image tag,
    so the key needs no `image_id` of its own), `builder_input`
    (`allowed_paths` shapes `t1_scope`), `verification` (check configs,
    budgets, seeds), and `verifier_revision()`.

    This is the VERIFY half of the two-key design (decision 5); the other
    half is `skeptic.collector.collect_pair`'s `baseline_cache`, keyed on
    `COLLECTOR_VERSION`. The two age differently on purpose: a detector edit
    moves `verifier_revision()` and re-verdicts every cached pair with no
    re-collection, while a collector behavior change never touches this key
    and needs `COLLECTOR_VERSION` bumped by hand to invalidate a baseline
    cached under the old behavior.
    """
    from skeptic.orchestrator import verifier_revision
    from skeptic.trace import config_hash

    variant_patch = hashlib.sha256(Path(variant.patch).read_bytes()).hexdigest()
    seed_patch = hashlib.sha256(Path(spec.seed.bug_patch).read_bytes()).hexdigest()
    return config_hash({
        "stage": "VERIFY", "task": spec.task_id, "variant": variant.id,
        "variant_patch": variant_patch,
        "seed": {**spec.seed.model_dump(), "bug_patch": seed_patch},
        "commit": spec.repo.commit,
        "environment": spec.environment.model_dump(),
        "builder_input": spec.builder_input.model_dump(),
        "verification": spec.verification.model_dump(),
        "verifier_revision": verifier_revision(),
    })


@app.command()
def verify(
    task: str = typer.Option(..., "--task"),
    variant: str = typer.Option(..., "--variant", help="Variant id from evaluation.variants."),
    profile: str = typer.Option("deterministic", "--profile"),
    tasks_dir: Path = typer.Option(Path("tasks"), "--tasks-dir"),  # noqa: B008
    workdir: Path = typer.Option(Path("workdir"), "--workdir"),  # noqa: B008
    runner: str = typer.Option("docker", "--runner", help="docker; venv verify is not wired yet."),
) -> None:
    """Run the deterministic check layer against a task's variant."""
    import json
    import shutil
    import time

    from skeptic.candidate import extract_candidate, snapshot
    from skeptic.checks import run_verify_layer
    from skeptic.checks.aggregate import aggregate, exit_code
    from skeptic.checks.evidence import Verdict
    from skeptic.checks.t1_outcomes import compute_fix_verified
    from skeptic.collector import collect_pair, observe_mutation
    from skeptic.image import repo_image_tag
    from skeptic.mutation import FULL_SUITE, generate_mutants, sample_mutants, select_tests
    from skeptic.orchestrator import StageCache, run_stage
    from skeptic.spec import find_task
    from skeptic.trace import TraceWriter, config_hash
    from skeptic.workspace import apply_patch, clone_pinned, materialize

    try:
        if profile != "deterministic":
            typer.echo(
                f"Unknown profile {profile!r}: skeptic verify only runs the "
                f"deterministic lane (T1's structural and coverage checks, "
                f"no paid calls). The mutation/adversarial-tests/judge "
                f"profile is wave B work and is not built yet. Next: re-run "
                f"with `--profile deterministic`."
            )
            raise typer.Exit(EXIT_INFRA)
        if runner != "docker":
            typer.echo(
                f"Unknown runner {runner!r}: skeptic verify runs in docker "
                f"only. VERIFY's read-only mounts and drop-on-missing "
                f"policy (RunContainer) have no venv equivalent, so the "
                f"venv fallback is not wired. Next: re-run with "
                f"`--runner docker`."
            )
            raise typer.Exit(EXIT_INFRA)
        if not _docker_available():
            typer.echo(
                "Docker daemon unavailable. VERIFY observes both trees "
                "inside throwaway containers; there is no reduced-isolation "
                "fallback for VERIFY. Next: start Docker Desktop, then "
                "re-run."
            )
            raise typer.Exit(EXIT_INFRA)

        spec = find_task(task, tasks_dir)
        workdir = workdir.resolve()
        verify_dir = workdir / spec.task_id / "verify" / variant
        trace = TraceWriter(
            verify_dir / "trace.jsonl",
            run_id=f"verify-{config_hash({'task': spec.task_id, 'variant': variant})}",
            task_id=spec.task_id)
        trace.event(stage="LOAD", actor="orchestrator", event="spec_loaded")

        known = sorted(v.id for v in spec.evaluation.variants)
        if variant not in known:
            typer.echo(
                f"No variant named {variant!r} in {spec.task_id}'s "
                f"evaluation.variants (known: {known or 'none'}). Skeptic "
                f"resolves --variant to one of the ids listed there. Next: "
                f"pick one of the known ids, or add a new entry to "
                f"evaluation.variants in the task spec."
            )
            raise typer.Exit(EXIT_INFRA)
        variant_spec = next(v for v in spec.evaluation.variants if v.id == variant)

        cache_key = _verify_cache_key(spec, variant_spec)

        def do_verify() -> dict:
            repo_dir = clone_pinned(spec.repo.url, spec.repo.commit,
                                    workdir / spec.task_id / "repo-cache")
            seeded = verify_dir / "seeded"
            variant_tree = verify_dir / "variant-tree"
            for stale in (seeded, variant_tree):
                if stale.exists():
                    shutil.rmtree(stale)
            materialize(repo_dir, spec.repo.commit, seeded)
            apply_patch(seeded, Path(spec.seed.bug_patch))
            snapshot(seeded, variant_tree)
            apply_patch(variant_tree, Path(variant_spec.patch))

            report = extract_candidate(
                seeded, variant_tree, verify_dir / "candidate.diff",
                allowed_paths=spec.builder_input.allowed_paths)

            pair = collect_pair(
                spec, repo_dir, report, verify_dir / "collect",
                baseline_cache=workdir / spec.task_id / "baseline-cache")

            # Mutation enrichment: generate -> sample -> select per mutant ->
            # execute, then fold the report onto the candidate side. Isolated
            # the way a registered check is (decision 8's `except Exception`
            # breadth, `checks/aggregate.py`'s own docstring): an enrichment
            # bug that killed sibling evidence would violate the same
            # coexistence principle `run_verify_layer` exists to hold, and a
            # narrower `SkepticInfraError`-only catch would leave a plain
            # `AttributeError` fatal. On capture, `candidate.mutation` stays
            # `None`, which is exactly what `t2_mutation.run`'s own INFRA
            # branch reads as "unobserved," so the failure surfaces as one
            # more per-check capture in `run_verify_layer` rather than as a
            # dead VERIFY run. `BaseException` (Ctrl-C, `SystemExit`) is not
            # caught here and still propagates.
            try:
                mutants = generate_mutants(pair)
                sampled = sample_mutants(
                    mutants, spec.verification.mutation.budget_mutants,
                    spec.verification.mutation.seed)
                selections: dict[str, tuple[str, ...] | None] = {}
                for mutant in sampled:
                    if mutant.population == "caller":
                        selections[mutant.mutant_id] = FULL_SUITE
                    else:
                        selections[mutant.mutant_id] = select_tests(
                            pair.candidate.coverage, pair.candidate.collected,
                            mutant.path, mutant.line)
                mutation_started = time.monotonic()
                mutation_report = observe_mutation(
                    spec, repo_image_tag(spec), pair.candidate.tree,
                    pair.artifacts_dir / "mutation", sampled, selections)
                for record in mutation_report.records:
                    trace.event(
                        stage="VERIFY", actor="checks.t2_mutation", event="mutant_result",
                        variant=variant_spec.id, dur_ms=record.dur_ms,
                        payload={"mutant_id": record.mutant_id, "status": record.status,
                                "tests_run": len(record.tests_run)})
                trace.event(
                    stage="VERIFY", actor="checks.t2_mutation", event="mutation_batch",
                    variant=variant_spec.id,
                    dur_ms=int((time.monotonic() - mutation_started) * 1000),
                    payload={"seed": mutation_report.seed, "budget": mutation_report.budget,
                            "generated": mutation_report.generated})
                pair = pair.model_copy(update={
                    "candidate": pair.candidate.model_copy(
                        update={"mutation": mutation_report})})
            except Exception as exc:  # noqa: BLE001 - decision 8, see comment above
                trace.event(
                    stage="VERIFY", actor="checks.t2_mutation",
                    event="mutation_enrichment_failed", variant=variant_spec.id,
                    payload={"error": f"{type(exc).__name__}: {exc}"})

            layer = run_verify_layer(pair)
            verdict = aggregate(
                layer, run_id=trace.run_id, task_id=spec.task_id,
                variant=variant_spec.id, isolation="docker-run",
                profile="deterministic")
            return {
                **verdict.model_dump(),
                "fix_verified": compute_fix_verified(pair),
                "artifacts_dir": str(pair.artifacts_dir),
            }

        cache = StageCache(verify_dir / "cache")
        cache_hit = cache.get(cache_key) is not None
        outcome = run_stage(cache, "VERIFY", cache_key, do_verify, trace)

        # Written here, after run_stage, rather than inside do_verify: a
        # cache hit never calls do_verify, and the artifacts directory it
        # would have written into may not even exist on this run (it is
        # scratch space, not part of the cache). This is build's
        # unconditional result.json write, generalized to VERIFY's shape.
        verdict_payload = {k: v for k, v in outcome.items()
                           if k not in ("fix_verified", "artifacts_dir")}
        verdict = Verdict.model_validate(verdict_payload)
        artifacts_dir = Path(outcome["artifacts_dir"])
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        (artifacts_dir / "verdict.json").write_text(
            json.dumps(verdict_payload, indent=2, sort_keys=True) + "\n")
        marker = " (cached)" if cache_hit else ""
        if verdict.status == "INFRA_ERROR":
            typer.echo(f"INFRA ERROR: {verdict.infra_reason}{marker}")
        else:
            typer.echo(f"VERDICT {verdict.verdict}{marker}")
        typer.echo(f"score {verdict.suspect_score:.2f}")
        for e in verdict.evidence:
            typer.echo(f"{e.check} · {e.rule} · {e.category} · {e.severity} · "
                       f"{e.location or '-'} · {e.artifact}")
        typer.echo(f"checks: {len(verdict.checks_completed)} completed · "
                   f"{len(verdict.not_applicable)} n/a · "
                   f"{len(verdict.checks_infra)} infra")
        typer.echo(f"fix_verified: {outcome['fix_verified']}")
        typer.echo(f"profile {verdict.profile} · isolation {verdict.isolation}")
        raise typer.Exit(exit_code(verdict))
    except SkepticInfraError as exc:
        typer.echo(f"INFRA ERROR: {exc}")
        raise typer.Exit(EXIT_INFRA) from exc
