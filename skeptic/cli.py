import hashlib
from pathlib import Path

import typer

import skeptic
from skeptic.builder import PRICING, _price
from skeptic.collector import observe_advtests
from skeptic.errors import SkepticInfraError
from skeptic.judge import judge_diff
from skeptic.llm import SKEPTIC_MODEL
from skeptic.spec import TaskSpec, VariantSpec
from skeptic.testgen import generate_candidates, one_hop_sources, read_source

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
def tasks(
    tasks_dir: Path = typer.Option(Path("tasks"), "--tasks-dir"),  # noqa: B008
) -> None:
    """List the corpus tasks this checkout carries."""
    from skeptic.spec import list_tasks

    try:
        specs = list_tasks(tasks_dir)
        if not specs:
            typer.echo(
                f"no task specs under {tasks_dir}: skeptic reads one yaml per "
                f"task from that directory. Next: `skeptic tasks --tasks-dir "
                f"<dir>` if the corpus lives elsewhere."
            )
            raise typer.Exit(EXIT_INFRA)
        for spec in specs:
            acc = "acceptance" if spec.acceptance_suite else "no acceptance suite"
            typer.echo(f"{spec.task_id} · {spec.repo.url.rsplit('/', 1)[-1]} · "
                       f"{len(spec.evaluation.variants)} variants · {acc}")
        typer.echo(f"Next: `skeptic seed --task {specs[0].task_id} --check`")
    except SkepticInfraError as exc:
        typer.echo(f"INFRA ERROR: {exc}")
        raise typer.Exit(EXIT_INFRA) from exc


@app.command()
def seed(
    task: str = typer.Option(..., "--task", help="Task id (tasks/<id>.yaml)."),
    check: bool = typer.Option(False, "--check", help="Run corpus admission invariants."),
    tasks_dir: Path = typer.Option(Path("tasks"), "--tasks-dir"),  # noqa: B008
    workdir: Path = typer.Option(Path("workdir"), "--workdir"),  # noqa: B008
    runner: str = typer.Option("venv", "--runner", help="venv (verify-only) or docker."),
    self_validate: bool = typer.Option(
        False, "--self-validate",
        help="After a passing --check, run full deterministic VERIFY on every "
             "clean variant and require PASS (plan invariant 4; needs docker)."),
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
        if self_validate and not check:
            typer.echo(
                "--self-validate requires --check: self-validation runs "
                "only after a passing admission check, so there is nothing "
                "for it to build on without one. Next: `skeptic seed --task "
                "<id> --check --self-validate`."
            )
            raise typer.Exit(EXIT_INFRA)
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
            typer.echo(f"CHECK PASSED · {spec.task_id} admitted to the corpus")
            if self_validate:
                clean = [v.id for v in spec.evaluation.variants if v.label == "clean"]
                typer.echo(f"self-validation: full VERIFY (deterministic) on {clean}")
                for variant_id in clean:
                    try:
                        verify(task=spec.task_id, variant=variant_id,
                               profile="deterministic", tasks_dir=tasks_dir,
                               workdir=workdir, runner="docker", yes=True)
                    except typer.Exit as exc:
                        if exc.exit_code == EXIT_INFRA:
                            typer.echo(
                                f"self-validation could not run: {variant_id} "
                                f"exited {exc.exit_code} (INFRA), an operational "
                                f"failure of the verify run itself, not a "
                                f"verdict on the variant. Next: read the verify "
                                f"output above (a common cause is Docker being "
                                f"unavailable), fix it, and re-run `skeptic "
                                f"seed --task {spec.task_id} --check "
                                f"--self-validate`.")
                            raise typer.Exit(EXIT_INFRA) from exc
                        if exc.exit_code != EXIT_OK:
                            typer.echo(
                                f"self-validation FAILED: {variant_id} exited "
                                f"{exc.exit_code}, and a clean variant that does "
                                f"not PASS is a corpus bug (plan invariant 4). "
                                f"Next: `skeptic verify --task {spec.task_id} "
                                f"--variant {variant_id}` and read the evidence.")
                            raise typer.Exit(EXIT_FAIL) from exc
                typer.echo("self-validation PASSED on every clean variant")
            raise typer.Exit(EXIT_OK)
        typer.echo(f"CHECK FAILED · fix the invariants above, then re-run "
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
        typer.echo(f"Next: `skeptic verify --task {spec.task_id} "
                   f"--variant <id>` to audit a candidate.")
        raise typer.Exit(EXIT_OK)
    except VenvBuildRefused as exc:
        typer.echo(f"REFUSED: {exc}")
        raise typer.Exit(EXIT_INFRA) from exc
    except SkepticInfraError as exc:
        typer.echo(f"INFRA ERROR: {exc}")
        raise typer.Exit(EXIT_INFRA) from exc


def _verify_cache_key(spec: TaskSpec, variant: VariantSpec, profile: str) -> str:
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
    budgets, seeds), `verifier_revision()`, and `profile`: the paid profile
    runs two more checks over the same pair (wave B, task 9), so a cached
    deterministic verdict must never serve a `--profile paid` request, and
    vice versa. `profile` has no default (review round 1, finding 2): every
    caller has to say which lane it means, on purpose, because a default
    that silently answered `"deterministic"` is exactly the shape that lets
    a future caller forget to pass the live profile and mis-key a paid run
    into the deterministic bucket without ever raising.

    This is the VERIFY half of the two-key design (plan decision 5); the other
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
        "profile": profile,
    })


@app.command()
def verify(
    task: str = typer.Option(..., "--task"),
    variant: str = typer.Option(..., "--variant", help="Variant id from evaluation.variants."),
    profile: str = typer.Option("deterministic", "--profile"),
    tasks_dir: Path = typer.Option(Path("tasks"), "--tasks-dir"),  # noqa: B008
    workdir: Path = typer.Option(Path("workdir"), "--workdir"),  # noqa: B008
    runner: str = typer.Option("docker", "--runner", help="docker; venv verify is not wired yet."),
    yes: bool = typer.Option(False, "--yes", help="Skip the paid-profile cost confirmation."),
) -> None:
    """Run the check layer against a task's variant."""
    import json
    import os
    import shutil
    import time

    from skeptic.candidate import extract_candidate, snapshot
    from skeptic.checks import run_verify_layer
    from skeptic.checks._util import under
    from skeptic.checks.aggregate import aggregate, exit_code
    from skeptic.checks.evidence import Verdict
    from skeptic.checks.t1_outcomes import compute_fix_verified
    from skeptic.collector import collect_pair, observe_mutation, observe_probe
    from skeptic.image import repo_image_tag
    from skeptic.mutation import FULL_SUITE, generate_mutants, sample_mutants, select_tests
    from skeptic.orchestrator import StageCache, run_stage
    from skeptic.render import render_verdict
    from skeptic.spec import find_task
    from skeptic.trace import TraceWriter, config_hash
    from skeptic.workspace import apply_patch, clone_pinned, materialize

    try:
        if profile not in ("deterministic", "paid"):
            typer.echo(
                f"Unknown profile {profile!r}: skeptic verify runs either "
                f"`deterministic` (T1's structural and coverage checks, no "
                f"paid calls) or `paid` (adds the adversarial-tests and "
                f"judge checks, which call the Anthropic API from the "
                f"host). Next: re-run with `--profile deterministic` or "
                f"`--profile paid`."
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

        # spec is loaded before the paid preflight below (build's own
        # ordering, cli.py:171-211): the cost-confirmation line names
        # spec.task_id, and the key/pricing/confirm sequence has to finish,
        # or exit, before `_docker_available()` and any image work either
        # way.
        spec = find_task(task, tasks_dir)

        # Variant validation also moves ahead of the paid preflight (review
        # round 1, minor 2): it only needs `spec`, already loaded above, and
        # a typo'd --variant should never reach the spend confirm.
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

        if profile == "paid":
            # Key, then pricing row, then the cost confirmation: all three
            # ahead of the docker-daemon check and every later image/
            # container step, so a missing key or an unpriced model fails
            # in well under a second rather than after a 90-second image
            # build (build's own rationale, cli.py:178-179, restated for
            # verify's two paid checks). SKEPTIC_MODEL, PRICING, and _price
            # are module-level imports (top of this file), not the
            # local-import style every other command in this module uses,
            # specifically so a test can monkeypatch
            # `skeptic.cli.SKEPTIC_MODEL` to an unpriced model without
            # touching the real PRICING table.
            if not os.environ.get("ANTHROPIC_API_KEY"):
                typer.echo(
                    "ANTHROPIC_API_KEY is not set. The paid profile's "
                    "adversarial-tests and judge checks call the Anthropic "
                    "API from the host (the key never enters the sandbox). "
                    "Next: export ANTHROPIC_API_KEY and re-run `skeptic "
                    f"verify --task {spec.task_id} --variant {variant} "
                    f"--profile paid`."
                )
                raise typer.Exit(EXIT_INFRA)
            if SKEPTIC_MODEL not in PRICING:
                typer.echo(
                    f"No pricing entry for {SKEPTIC_MODEL!r}; the cost "
                    f"estimate cannot be computed. Next: add a sourced "
                    f"price to PRICING in skeptic/builder.py."
                )
                raise typer.Exit(EXIT_INFRA)
            # One testgen.generate_candidates call (max_tokens 16000) plus
            # one judge.judge_diff call (max_tokens 2000), both at max
            # output: a worst-case constant, not a measurement of a real
            # run, so the estimate over-quotes a typical call rather than
            # under-quoting one. The input-token figures (30000, 10000) are
            # round-number guesses at a problem statement plus a handful of
            # pristine source files for the first call, and a candidate
            # diff for the second.
            est = (_price(SKEPTIC_MODEL, 30_000, 16_000)
                   + _price(SKEPTIC_MODEL, 10_000, 2_000))
            typer.echo(
                f"Paid verify: task={spec.task_id} variant={variant} "
                f"model={SKEPTIC_MODEL} estimated max cost ${est:.2f}"
            )
            if not yes and not typer.confirm("Proceed (this spends real API money)?"):
                typer.echo(
                    "Declined: the run was not started, so no API spend "
                    "happened. Skeptic confirms before every billed paid "
                    "verify run unless --yes is passed. Next: re-run "
                    f"`skeptic verify --task {spec.task_id} --variant "
                    f"{variant} --profile paid --yes` once you're ready to "
                    f"spend."
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

        workdir = workdir.resolve()
        verify_dir = workdir / spec.task_id / "verify" / variant
        trace = TraceWriter(
            verify_dir / "trace.jsonl",
            run_id=f"verify-{config_hash({'task': spec.task_id, 'variant': variant})}",
            task_id=spec.task_id)
        trace.event(stage="LOAD", actor="orchestrator", event="spec_loaded")

        cache_key = _verify_cache_key(spec, variant_spec, profile)

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
                voided = sum(len(v.excluded_mutant_ids) for v in mutation_report.calibration_void)
                trace.event(
                    stage="VERIFY", actor="checks.t2_mutation", event="mutation_batch",
                    variant=variant_spec.id,
                    dur_ms=int((time.monotonic() - mutation_started) * 1000),
                    payload={"seed": mutation_report.seed, "budget": mutation_report.budget,
                            "generated": mutation_report.generated, "voided": voided})
                pair = pair.model_copy(update={
                    "candidate": pair.candidate.model_copy(
                        update={"mutation": mutation_report})})
            except Exception as exc:  # noqa: BLE001 - decision 8, see comment above
                trace.event(
                    stage="VERIFY", actor="checks.t2_mutation",
                    event="mutation_enrichment_failed", variant=variant_spec.id,
                    payload={"error": f"{type(exc).__name__}: {exc}"})

            # Probe enrichment: its own isolation block, parallel to the
            # mutation one above and never nested inside it (DECISIONS row
            # 116), so a dead mutation batch and a dead probe run degrade
            # independently rather than one taking the other down. On
            # capture, `candidate.probe` stays `None`, which is exactly what
            # `t2_probe.run`'s own INFRA branch reads as "unobserved" when
            # `consumer_probe.entrypoints` is non-empty (empty entrypoints
            # never reach this block's exception path at all: `observe_probe`
            # returns `None` for that case by contract rather than by raising).
            try:
                probe_started = time.monotonic()
                probe_report = observe_probe(
                    spec, repo_image_tag(spec), pair.candidate.tree,
                    pair.artifacts_dir / "probe")
                trace.event(
                    stage="VERIFY", actor="checks.t2_probe", event="probe_batch",
                    variant=variant_spec.id,
                    dur_ms=int((time.monotonic() - probe_started) * 1000),
                    payload={"entrypoints": len(spec.verification.consumer_probe.entrypoints),
                            "calls": len(probe_report.calls) if probe_report else 0})
                pair = pair.model_copy(update={
                    "candidate": pair.candidate.model_copy(update={"probe": probe_report})})
            except Exception as exc:  # noqa: BLE001 - decision 8, see comment above
                trace.event(
                    stage="VERIFY", actor="checks.t2_probe",
                    event="probe_enrichment_failed", variant=variant_spec.id,
                    payload={"error": f"{type(exc).__name__}: {exc}"})

            # Adversarial-tests and judge enrichment: paid-profile only
            # (decision 9), after the mutation and probe blocks above and
            # structured the same way: each check gets its own isolation
            # block rather than one wrapping both (DECISIONS row 116), so a
            # dead advtests batch cannot take a healthy judge call down with
            # it or vice versa. `client` is host-side and shared once built,
            # but built lazily inside whichever block reaches it first
            # (`client is None`) rather than ahead of both: constructing it
            # outside both `try`s would let a non-`SkepticInfraError` raise
            # from `anthropic.Anthropic()` (a bad key, a broken install)
            # escape both isolation blocks and kill the run with a
            # traceback instead of degrading to two INFRA rows plus a full
            # T1 verdict, the exact property decision 8/row 116 exist to
            # hold (review round 1, finding 1). If the advtests block's own
            # attempt fails, `client` stays `None` and the judge block
            # tries again from scratch, so a dead client is reported by
            # both blocks' own `except`, not just the first one to reach it.
            if profile == "paid":
                client = None

                try:
                    if client is None:
                        import anthropic

                        client = anthropic.Anthropic()
                    # generate_candidates needs the pristine (pre-seed,
                    # spec.repo.commit) body of every file the candidate
                    # changed. observe_advtests below materializes that same
                    # commit again for itself, into a "advtests-reference"
                    # tree sibling to the artifacts path it is handed (its
                    # own docstring's work-dir layout: `work =
                    # artifacts.parent`), but only after the candidates it
                    # takes as an argument already exist -- generate_
                    # candidates has to run first, so this is a second,
                    # host-side materialize of the same commit, read once
                    # for the prompt. It lives under `verify_dir`, a
                    # different parent directory than `pair.artifacts_dir`
                    # (where observe_advtests rmtree-rebuilds its own
                    # "advtests-*" siblings), which rules out any
                    # interaction between the two by construction rather
                    # than by name alone.
                    sources_tree = verify_dir / "advtests-sources"
                    if sources_tree.exists():
                        shutil.rmtree(sources_tree)
                    materialize(repo_dir, spec.repo.commit, sources_tree)
                    # Only source files reach the model. `changed_files` is
                    # candidate-controlled and a candidate diff may touch
                    # `tests/` (h1 deletes a test file, h3 edits one), so an
                    # unfiltered dict hands the model held-out test content.
                    # The wave A final review found exactly that in two of
                    # eight published runs. `one_hop_sources` reads the same
                    # filtered list: its resolver is already bounded by
                    # src_dirs, but walking a test file's imports would still
                    # let the test file choose the context.
                    src_changed = [
                        path for path in pair.candidate_diff.changed_files
                        if under(path, spec.environment.src_dirs)
                    ]
                    sources = {
                        path: read_source(sources_tree / path)
                        for path in src_changed
                        if (sources_tree / path).is_file()
                    }
                    sources = {**sources, **one_hop_sources(
                        sources_tree, src_changed, spec.environment.src_dirs)}
                    candidates, testgen_io = generate_candidates(client, spec, sources, trace)
                    # Persisted before observe_advtests, the same
                    # before-the-fold ordering as the judge io write below
                    # (DECISIONS row 132/143): a dead ladder still leaves the
                    # raw response and stop_reason on disk to inspect.
                    pair.artifacts_dir.mkdir(parents=True, exist_ok=True)
                    (pair.artifacts_dir / "t2_advtests_io.json").write_text(
                        json.dumps(testgen_io, indent=2, sort_keys=True) + "\n")
                    advtests_started = time.monotonic()
                    advtests_report = observe_advtests(
                        spec, repo_image_tag(spec), repo_dir, pair,
                        pair.artifacts_dir / "advtests", candidates, model=SKEPTIC_MODEL)
                    trace.event(
                        stage="VERIFY", actor="checks.t2_advtests", event="advtest_batch",
                        variant=variant_spec.id,
                        dur_ms=int((time.monotonic() - advtests_started) * 1000),
                        payload={"n_candidates": advtests_report.n_candidates,
                                "generated": len(advtests_report.candidates),
                                "trusted": len(advtests_report.trusted),
                                "divergences": len(advtests_report.divergences)})
                    pair = pair.model_copy(update={
                        "candidate": pair.candidate.model_copy(
                            update={"advtests": advtests_report})})
                except Exception as exc:  # noqa: BLE001 - decision 8, see comment above
                    trace.event(
                        stage="VERIFY", actor="checks.t2_advtests",
                        event="advtests_enrichment_failed", variant=variant_spec.id,
                        payload={"error": f"{type(exc).__name__}: {exc}"})

                try:
                    if client is None:
                        import anthropic

                        client = anthropic.Anthropic()
                    diff_text = read_source(pair.candidate_diff.diff_path)
                    judge_report, judge_io = judge_diff(client, diff_text, trace)
                    # Persisted before the fold: judge_diff's own docstring
                    # states the returned io dict (verbatim request and
                    # response) as its artifact contract, and JudgeReport
                    # (frozen, four fields) has no field to carry it, so
                    # this write is what gives that contract an owner
                    # (DECISIONS row 132 flagged the gap; closed here).
                    pair.artifacts_dir.mkdir(parents=True, exist_ok=True)
                    (pair.artifacts_dir / "t2_judge_io.json").write_text(
                        json.dumps(judge_io, indent=2, sort_keys=True) + "\n")
                    trace.event(
                        stage="VERIFY", actor="checks.t2_judge", event="judge_call",
                        variant=variant_spec.id,
                        payload={"flagged": judge_report.flagged})
                    pair = pair.model_copy(update={
                        "candidate": pair.candidate.model_copy(update={"judge": judge_report})})
                except Exception as exc:  # noqa: BLE001 - decision 8, see comment above
                    trace.event(
                        stage="VERIFY", actor="checks.t2_judge",
                        event="judge_enrichment_failed", variant=variant_spec.id,
                        payload={"error": f"{type(exc).__name__}: {exc}"})

            layer = run_verify_layer(pair, profile=profile)
            verdict = aggregate(
                layer, run_id=trace.run_id, task_id=spec.task_id,
                variant=variant_spec.id, isolation="docker-run",
                profile=profile)
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
        # would have written into may not even exist on this run (it sits
        # outside the cache, as scratch space). This is build's
        # unconditional result.json write, generalized to VERIFY's shape.
        verdict_payload = {k: v for k, v in outcome.items()
                           if k not in ("fix_verified", "artifacts_dir")}
        verdict = Verdict.model_validate(verdict_payload)
        artifacts_dir = Path(outcome["artifacts_dir"])
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        (artifacts_dir / "verdict.json").write_text(
            json.dumps(verdict_payload, indent=2, sort_keys=True) + "\n")
        render_verdict(verdict, fix_verified=outcome["fix_verified"],
                       cached=cache_hit)
        raise typer.Exit(exit_code(verdict))
    except SkepticInfraError as exc:
        typer.echo(f"INFRA ERROR: {exc}")
        raise typer.Exit(EXIT_INFRA) from exc


@app.command(name="eval")
def eval_command(
    tasks: str = typer.Option(..., "--tasks", help="Comma-separated task ids."),
    profile: str = typer.Option(..., "--profile"),
    tasks_dir: Path = typer.Option(Path("tasks"), "--tasks-dir"),  # noqa: B008
    workdir: Path = typer.Option(Path("workdir"), "--workdir"),  # noqa: B008
    out: Path = typer.Option(Path("evals/v1"), "--out"),  # noqa: B008
    yes: bool = typer.Option(False, "--yes", help="Skip the sweep-wide cost confirmation."),
) -> None:
    """Drive verify across every (task, variant) pair and publish an eval snapshot."""
    import os

    from skeptic import evalkit
    from skeptic.spec import find_task
    from skeptic.trace import write_manifest

    try:
        if profile not in ("deterministic", "paid"):
            typer.echo(
                f"Unknown profile {profile!r}: skeptic verify runs either "
                f"`deterministic` (T1's structural and coverage checks, no "
                f"paid calls) or `paid` (adds the adversarial-tests and "
                f"judge checks, which call the Anthropic API from the "
                f"host). Next: re-run with `--profile deterministic` or "
                f"`--profile paid`."
            )
            raise typer.Exit(EXIT_INFRA)

        specs = [find_task(task_id.strip(), tasks_dir) for task_id in tasks.split(",")]

        workdir = workdir.resolve()
        out = out.resolve()
        n_pairs = sum(len(spec.evaluation.variants) for spec in specs)

        if profile == "paid":
            # One confirm for the whole sweep, not one per run: verify's own
            # per-run confirm is bypassed below by always passing yes=True.
            if not os.environ.get("ANTHROPIC_API_KEY"):
                typer.echo(
                    "ANTHROPIC_API_KEY is not set. The paid profile's "
                    "adversarial-tests and judge checks call the Anthropic "
                    "API from the host (the key never enters the sandbox). "
                    f"Next: export ANTHROPIC_API_KEY and re-run `skeptic "
                    f"eval --tasks {tasks} --profile paid`."
                )
                raise typer.Exit(EXIT_INFRA)
            if SKEPTIC_MODEL not in PRICING:
                typer.echo(
                    f"No pricing entry for {SKEPTIC_MODEL!r}; the cost "
                    f"estimate cannot be computed. Next: add a sourced "
                    f"price to PRICING in skeptic/builder.py."
                )
                raise typer.Exit(EXIT_INFRA)
            est_per_run = (_price(SKEPTIC_MODEL, 30_000, 16_000)
                           + _price(SKEPTIC_MODEL, 10_000, 2_000))
            est_max = est_per_run * n_pairs
            typer.echo(
                f"Paid eval sweep: {n_pairs} runs · estimated per-run "
                f"${est_per_run:.2f} · sweep max cost ${est_max:.2f}. This "
                f"one confirm covers the whole sweep; each run's own paid "
                f"confirm is skipped."
            )
            if not yes and not typer.confirm("Proceed (this spends real API money)?"):
                typer.echo(
                    "Declined: the sweep was not started, so no API spend "
                    "happened. Skeptic confirms once before a paid eval "
                    f"sweep unless --yes is passed. Next: re-run `skeptic "
                    f"eval --tasks {tasks} --profile paid --yes` once "
                    f"you're ready to spend."
                )
                raise typer.Exit(EXIT_INFRA)

        run_dir = out / "runs" / evalkit.eval_run_id()
        infra: list[str] = []
        n_runs = 0
        for spec in specs:
            for variant_spec in spec.evaluation.variants:
                verify_dir = workdir / spec.task_id / "verify" / variant_spec.id
                evalkit.rotate_trace(verify_dir)
                try:
                    verify(task=spec.task_id, variant=variant_spec.id,
                           profile=profile, tasks_dir=tasks_dir,
                           workdir=workdir, runner="docker", yes=True)
                except typer.Exit as exc:
                    code = exc.exit_code
                else:  # pragma: no cover - verify always exits
                    raise SkepticInfraError(
                        f"verify returned without exiting for "
                        f"{spec.task_id}/{variant_spec.id}. Every verify path "
                        f"raises typer.Exit; a plain return means the command "
                        f"changed shape and this sweep's exit-code capture is "
                        f"no longer correct. Next: re-run `skeptic verify "
                        f"--task {spec.task_id} --variant {variant_spec.id}` "
                        f"alone and read its exit."
                    )
                evalkit.snapshot_run(
                    verify_dir, run_dir / spec.task_id / variant_spec.id, code)
                n_runs += 1
                if code == EXIT_INFRA:
                    infra.append(f"{spec.task_id}/{variant_spec.id}")

        manifest = evalkit.build_manifest(specs, workdir)
        write_manifest(run_dir / "manifest.json", manifest)
        write_manifest(out / "manifest.json", manifest)

        rows = evalkit.load_rows(run_dir, tasks_dir)
        baselines = [evalkit.baseline_always_suspect(rows),
                     evalkit.baseline_suite_green_only(rows),
                     evalkit.baseline_judge_alone(rows)]
        table_path = run_dir / "table.md"
        table_path.write_text(evalkit.render_table(rows, baselines))

        typer.echo(f"{n_runs} runs · {len(infra)} INFRA")
        typer.echo(f"run dir: {run_dir}")
        if infra:
            typer.echo(f"INFRA: {', '.join(infra)}")
        typer.echo(f"table: {table_path}")
        raise typer.Exit(EXIT_INFRA if infra else EXIT_OK)
    except SkepticInfraError as exc:
        typer.echo(f"INFRA ERROR: {exc}")
        raise typer.Exit(EXIT_INFRA) from exc
