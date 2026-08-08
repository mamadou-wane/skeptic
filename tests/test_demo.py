"""`skeptic demo` runs the real thing, and these tests run it for real.

Each of the first three invocations copies three trees and starts six pytest
subprocesses. That cost is the point: a faked pytest would exercise the
plumbing and leave the claim the demo exists to make (two honest verdicts from
a keyless, dockerless, network-free run) unverified.
"""
from pathlib import Path

import pytest
from typer.testing import CliRunner

from skeptic.cli import app

runner = CliRunner()


def test_demo_prints_both_verdicts_and_zero_cost():
    result = runner.invoke(app, ["demo"])

    assert result.exit_code == 0, result.output
    assert result.output.index("VERDICT PASS") < result.output.index("VERDICT FAIL")
    assert "t1_collect" in result.output
    assert "test_golden.py" in result.output      # the vanished nodeid, cited
    assert "Cost: $0.00" in result.output


def test_demo_touches_neither_docker_nor_the_network(monkeypatch):
    monkeypatch.setattr("skeptic.cli._docker_available",
                        lambda: pytest.fail("demo asked about docker"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    assert runner.invoke(app, ["demo"]).exit_code == 0


def test_demo_is_plain_under_no_color(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")

    assert "\x1b[" not in runner.invoke(app, ["demo"]).output


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
