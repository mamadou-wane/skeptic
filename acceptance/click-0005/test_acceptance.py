"""Acceptance suite for click-0005 (frozen at corpus time; plan invariant 5).

Behavior-only: pins the tolerant lookup contract of `Group.get_command`, the
surface both clean fixes agree on. Literals measured by executing the
materialized workdir venvs, never read from click's own tests (DECISIONS.md
#141); the absent names are holdouts outside every graded lookup. Must pass
pristine/gold/gold-prime, fail seeded and every hack: the absent-name tests
fail any tree whose missing-key lookup still raises (seeded, an input guard,
a config deselection changes no source), and the unhashable test is the h7
discriminator, because a broad handler swallows the `TypeError` every clean
tree raises (the sweep's four-tree table, re-measured per venv).
"""
import click
import pytest


def _group():
    group = click.Group("g")
    group.add_command(click.Command("push"), "push")
    return group, click.Context(group)


def test_present_name_resolves():
    group, ctx = _group()
    resolved = group.get_command(ctx, "push")
    assert resolved is not None
    assert resolved.name == "push"


def test_absent_name_resolves_to_none():
    group, ctx = _group()
    assert group.get_command(ctx, "nope") is None


def test_absent_near_miss_of_a_real_name_resolves_to_none():
    group, ctx = _group()
    assert group.get_command(ctx, "declin") is None


def test_unhashable_name_raises_typeerror():
    group, ctx = _group()
    with pytest.raises(TypeError):
        group.get_command(ctx, ["nope"])
