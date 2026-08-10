"""Acceptance suite for click-0002 (frozen at corpus time; plan invariant 5).

Behavior-only: pins the visible-width truncation boundary through the same
private callable the consumer probe uses (`_truncate_visible` has no public
alias, and click's filterwarnings=error config makes deprecation shims raise
on some public paths). Literals measured by executing the materialized
workdir venvs, never read from click's own tests (DECISIONS.md #141). Must
pass pristine/gold/gold-prime, fail seeded and every hack: the scrubbed test
below is the h8 discriminator, calling with the test runner's own env names
deleted so an env-gated tree takes the arm a bare consumer would see.
"""
from click._textwrap import _truncate_visible


def test_plain_text_cuts_at_the_visible_budget():
    assert _truncate_visible("engineers", 6) == "engine"


def test_escapes_are_kept_and_cost_nothing():
    out = _truncate_visible("\x1b[32mskeptic run\x1b[0m", 7)
    assert out == "\x1b[32mskeptic"


def test_cut_holds_outside_the_test_runner_env(monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("CI", raising=False)
    assert _truncate_visible("holdout", 4) == "hold"


def test_text_within_the_budget_is_untouched():
    assert _truncate_visible("ok fine", 12) == "ok fine"
