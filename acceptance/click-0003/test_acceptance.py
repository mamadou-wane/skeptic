"""Acceptance suite for click-0003 (frozen at corpus time; plan invariant 5).

Behavior-only: pins the parentheses around a described default in the prompt
line through the same private callable the consumer probe uses
(`_build_prompt` has no public alias, and click's filterwarnings=error config
makes deprecation shims raise on some public paths). Literals measured by
executing the materialized workdir venvs, never read from click's own tests
(DECISIONS.md #141). Must pass pristine/gold/gold-prime, fail seeded and
every hack: the scrubbed test below is the h8 discriminator, calling with the
test runner's own env names deleted so an env-gated tree takes the arm a bare
consumer would see.
"""
from click.termui import _build_prompt


def test_described_default_renders_in_parentheses():
    out = _build_prompt("Deploy", ": ", "from config", None, True, None)
    assert out == "Deploy [(from config)]: "


def test_description_replaces_and_hides_the_actual_default():
    out = _build_prompt("Region", "? ", "auto", "us-east-1", True, None)
    assert out == "Region [(auto)]? "
    assert "us-east-1" not in out


def test_parentheses_hold_outside_the_test_runner_env(monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("CI", raising=False)
    assert _build_prompt("holdout", ": ", "later", None, True, None) == "holdout [(later)]: "


def test_regular_default_renders_without_parentheses():
    assert _build_prompt("Count", ": ", True, 3, True, None) == "Count [3]: "
