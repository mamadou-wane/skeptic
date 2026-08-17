"""`skeptic demo` runs the real thing, and these tests run it for real.

Most invocations here copy three trees and start six pytest subprocesses. That
cost is the point: a faked pytest would exercise the plumbing and leave the
claim the demo exists to make (two honest verdicts from a keyless, dockerless,
network-free run) unverified.
"""
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from skeptic import demo
from skeptic.cli import app
from skeptic.spec import find_task
from tests.helpers import make_minirepo_task, run_on_a_tty

runner = CliRunner()

# Driven by the NO_COLOR test in a child process attached to a pty, which is
# the only place click's color decision is visible: `CliRunner` hands the
# command a pipe and click strips styling on any non-tty regardless.
_TTY_DEMO = """
from skeptic.cli import app
app()
"""


def test_demo_prints_both_verdicts_and_zero_cost():
    result = runner.invoke(app, ["demo"])

    assert result.exit_code == 0, result.output
    assert result.output.index("VERDICT PASS") < result.output.index("VERDICT FAIL")
    assert "t1_collect" in result.output
    assert "test_golden.py" in result.output      # the vanished nodeid, cited
    assert "Cost: $0.00" in result.output


def test_demo_touches_neither_docker_nor_the_network(monkeypatch):
    monkeypatch.setattr("skeptic.cli._docker_diagnosis",
                        lambda: pytest.fail("demo asked about docker"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    assert runner.invoke(app, ["demo"]).exit_code == 0


def test_demo_is_plain_under_no_color():
    """Run on a real tty, where the answer is not decided for us. The earlier
    version of this test invoked through `CliRunner` and would have passed
    against a `render.py` that never read NO_COLOR at all, which is what it
    was doing until the task 14 review measured it."""
    out = run_on_a_tty(_TTY_DEMO, ["demo"], env={"NO_COLOR": "1"})

    assert "VERDICT PASS" in out and "VERDICT FAIL" in out
    assert "\x1b[" not in out


def test_demo_refuses_without_git_rather_than_raising_filenotfound(monkeypatch):
    """`workspace.apply_patch` reaches `subprocess.run(["git", ...])` before
    any of its own return-code handling, so an absent binary surfaced as a
    `FileNotFoundError` traceback out of the first command a fresh install
    runs. The pre-flight in `demo` turns that into a sentence."""
    monkeypatch.setattr(shutil, "which", lambda name: None if name == "git" else name)

    result = runner.invoke(app, ["demo"])

    assert result.exit_code == 3
    assert "INFRA ERROR: No `git` binary on PATH" in result.output
    assert "Next: install git, then re-run `skeptic demo`." in result.output
    assert type(result.exception) is SystemExit      # typer.Exit, no traceback


def test_demo_exits_three_when_a_rendered_verdict_breaks_the_contract(monkeypatch):
    """The expected verdicts are the command's contract, so a demo that
    renders a different one has to say so in its exit code. Gold still
    renders PASS here; the contract is what moved."""
    monkeypatch.setattr("skeptic.demo.VARIANTS", (("gold", "FAIL", "the one-line fix"),))

    result = runner.invoke(app, ["demo"])

    assert result.exit_code == 3
    assert "demo FAILED: gold rendered PASS, expected FAIL" in result.output


def test_demo_names_the_reinstall_when_the_bundle_is_missing(monkeypatch):
    monkeypatch.setattr("skeptic.demo.fixtures_root", lambda: Path("/nope"))

    result = runner.invoke(app, ["demo"])

    assert result.exit_code == 3
    assert "pip install" in result.output


def test_demo_spec_has_not_drifted_from_the_helper_minirepo_task(tmp_path):
    """`demo._spec` hand-copies the task `tests/helpers.make_minirepo_task`
    writes, because a plain install ships no `tasks/` directory to read one
    from. Nothing but this test keeps the two equal, and a drift in the fields
    the checks read would make the demo audit a task the corpus does not have.
    """
    tasks_dir, task_id = make_minirepo_task(tmp_path)
    helper = find_task(task_id, tasks_dir)
    demo_spec = demo._spec(*demo._bundle())

    assert demo_spec.task_id == helper.task_id
    assert demo_spec.environment == helper.environment
    assert demo_spec.seed.failing_tests == helper.seed.failing_tests
    assert demo_spec.builder_input.allowed_paths == helper.builder_input.allowed_paths
