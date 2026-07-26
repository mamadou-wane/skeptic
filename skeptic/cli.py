from pathlib import Path

import typer

import skeptic
from skeptic.errors import SkepticInfraError
from skeptic.spec import TaskSpec

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
    from skeptic.builder import prompt_version
    from skeptic.trace import config_hash

    return config_hash({
        "stage": "BUILD", "task": spec.task_id, "seed": seed_hash,
        "model": model, "prompt": prompt_version(),
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

    from skeptic.builder import PRICING, run_build
    from skeptic.builder_tools import ToolContext
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
        baseline = build_dir / "baseline"
        for stale in (seeded, baseline):
            if stale.exists():
                shutil.rmtree(stale)
        materialize(repo, spec.repo.commit, seeded)
        apply_patch(seeded, Path(spec.seed.bug_patch))
        snapshot(seeded, baseline)

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
                ctx = ToolContext(workspace=seeded, session=session, spec=spec)
                result = run_build(spec, ctx, trace, model=model, client=client)
            report = extract_candidate(
                baseline, seeded, build_dir / "candidate.diff",
                allowed_paths=spec.builder_input.allowed_paths)
            return {
                "stop_reason": result.stop_reason, "iterations": result.iterations,
                "in_tokens": result.in_tokens, "out_tokens": result.out_tokens,
                "usd": round(result.usd, 4), "suite_green": result.suite_green,
                "candidate": str(report.diff_path),
                "changed_files": report.changed_files,
                "out_of_scope": report.out_of_scope,
                "is_empty": report.is_empty,
                "image_id": image.image_id,
            }

        outcome = run_stage(StageCache(build_dir / "cache"), "BUILD",
                            cache_key, do_build, trace)
        (build_dir / "result.json").write_text(json.dumps(outcome, indent=2) + "\n")
        typer.echo(f"stop: {outcome['stop_reason']} · iterations: "
                   f"{outcome['iterations']} · suite green: "
                   f"{outcome['suite_green']} · cost: ${outcome['usd']:.2f}")
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
