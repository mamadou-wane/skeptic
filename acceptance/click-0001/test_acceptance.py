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
