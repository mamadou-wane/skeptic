# tests/test_render.py
"""render_verdict/verdict_color: the echoing half of verify's old tail.

Fixtures build `Verdict` via `model_validate` over literal dicts (matching
`tests/test_evidence.py`'s round-trip convention) rather than the positional
`Verdict(...)` constructor, since the module under test never writes a
verdict, only reads one that already validated.
"""
from skeptic.checks.evidence import Verdict
from skeptic.render import render_verdict, verdict_color
from tests.helpers import run_on_a_tty

# Rendered in a child process attached to a pty by the two color tests. It
# rebuilds a FAIL verdict rather than importing one, since the fixtures here
# are module-level objects and the child gets source, never state.
_TTY_RENDER = """
from skeptic.checks.evidence import Verdict
from skeptic.render import render_verdict

render_verdict(Verdict.model_validate({
    "schema_version": 1, "run_id": "r_test", "task_id": "click-0001",
    "variant": "h1", "status": "ok", "verdict": "FAIL", "suspect_score": 0.0,
    "checks_completed": ["t1_collect"], "not_applicable": [],
    "checks_infra": [], "evidence": [], "isolation": "docker-run",
    "profile": "deterministic", "infra_reason": None,
}), fix_verified=False)
"""


def _verdict(**overrides):
    payload = {
        "schema_version": 1,
        "run_id": "r_test",
        "task_id": "click-0001",
        "variant": "h1",
        "status": "ok",
        "verdict": "PASS",
        "suspect_score": 0.0,
        "checks_completed": ["t1_collect"],
        "not_applicable": [],
        "checks_infra": [],
        "evidence": [],
        "isolation": "docker-run",
        "profile": "deterministic",
        "infra_reason": None,
    }
    payload.update(overrides)
    return Verdict.model_validate(payload)


PASS_VERDICT = _verdict()

FAIL_VERDICT = _verdict(
    verdict="FAIL",
    suspect_score=1.4,
    evidence=[{
        "check": "t1_collect",
        "rule": "collect_shrinkage",
        "category": "H1",
        "severity": "hard",
        "detail": "collect shrank",
        "artifact": "traces/r_test/t1_collect.json",
    }],
)

SUSPECT_VERDICT = _verdict(verdict="SUSPECT", suspect_score=0.6)

INFRA_VERDICT = _verdict(
    status="INFRA_ERROR",
    verdict=None,
    checks_completed=[],
    checks_infra=["t1_collect"],
    infra_reason="container died",
)


def test_render_verdict_prints_banner_score_and_evidence(capsys):
    render_verdict(FAIL_VERDICT, fix_verified=True)
    out = capsys.readouterr().out
    assert "VERDICT FAIL" in out
    assert "score 1.40" in out
    assert "t1_collect · collect_shrinkage · H1 · hard" in out
    assert "fix_verified: True" in out


def test_render_verdict_marks_a_cached_run(capsys):
    render_verdict(PASS_VERDICT, fix_verified=True, cached=True)
    assert "VERDICT PASS (cached)" in capsys.readouterr().out


def test_render_verdict_styles_the_banner_on_a_tty():
    """The control for the NO_COLOR test below. Without it, a rendering that
    stopped emitting color for any reason would make that test pass while
    proving nothing, which is the shape the `capsys` version of it had."""
    out = run_on_a_tty(_TTY_RENDER, env={"NO_COLOR": ""})

    assert "\x1b[" in out
    assert "VERDICT FAIL" in out


def test_render_verdict_is_plain_under_no_color():
    """Measured with typer 0.27 on 2026-08-08: `secho` emits the escape codes
    on a tty with NO_COLOR set, so `render._color` has to pass `color=False`
    itself. The predecessor of this test asserted through `capsys`, where
    click strips styling on any non-tty and the assertion held whether or not
    the variable was read at all."""
    out = run_on_a_tty(_TTY_RENDER, env={"NO_COLOR": "1"})

    assert "\x1b[" not in out
    assert "VERDICT FAIL" in out


def test_render_verdict_reports_infra_without_a_verdict_name(capsys):
    render_verdict(INFRA_VERDICT, fix_verified=None)
    out = capsys.readouterr().out
    assert "INFRA ERROR: container died" in out
    assert "VERDICT" not in out


def test_verdict_color_maps_each_status_to_its_color():
    assert verdict_color(PASS_VERDICT) == "green"
    assert verdict_color(SUSPECT_VERDICT) == "yellow"
    assert verdict_color(FAIL_VERDICT) == "red"
    assert verdict_color(INFRA_VERDICT) == "red"
