from pathlib import Path

import typer

import skeptic
from skeptic.errors import SkepticInfraError

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
