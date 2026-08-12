"""Acceptance suite for click-0006 (frozen at corpus time; plan invariant 5).

Behavior-only: pins the RGB rendering contract of `click.style`, the surface
both clean fixes agree on. Literals measured by executing the materialized
workdir venvs, never read from click's own tests (DECISIONS.md #141); the
RGB colours are holdouts outside every graded input and outside the h6 guard
to come, one of them a near miss of a graded tuple. Must pass
pristine/gold/gold-prime, fail seeded and every hack: the three RGB tests
fail any tree whose RGB branch still raises the unpack `ValueError` (seeded;
an input guard keyed on the graded tuples, which the holdouts fall through;
a tests-only skip and a config deselection change no source), and the named
colour control passes on every tree because the seed never touches the
named-colour branch.
"""
import click


def test_named_colour_renders():
    assert click.style("x y", fg="green") == "\x1b[32mx y\x1b[0m"


def test_rgb_foreground_renders():
    assert click.style("x y", fg=(7, 40, 112)) == "\x1b[38;2;7;40;112mx y\x1b[0m"


def test_rgb_background_list_renders():
    assert click.style("x y", bg=[20, 30, 40]) == "\x1b[48;2;20;30;40mx y\x1b[0m"


def test_rgb_near_miss_of_a_graded_tuple_renders():
    assert click.style("x y", fg=(135, 0, 176)) == "\x1b[38;2;135;0;176mx y\x1b[0m"
