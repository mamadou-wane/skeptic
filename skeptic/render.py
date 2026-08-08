"""Terminal rendering for a verdict, shared by `verify` and `demo`.

Split out of verify's tail, which also wrote verdict.json: the demo renders
real verdicts through the real aggregator but owns no run directory, so it
needs the echoing half and not the persisting half.

NO_COLOR is read here, explicitly. Task 9's brief and DECISIONS row 162 both
claimed click's `secho` already honors it; measured with typer 0.27 through a
pty on 2026-08-08, it does not. The banner comes out as
`\\x1b[31m\\x1b[1m...\\x1b[0m` on a tty whether or not the variable is set,
and the reason nothing caught it is that click strips styling on any non-tty,
so `capsys` and `CliRunner` both report plain text either way. Passing
`color=False` through to click's own `secho` does turn it off, measured the
same way, and that is what `_color` is for.
"""
from __future__ import annotations

import os

import typer

from skeptic.checks.evidence import Verdict

_COLORS = {"PASS": "green", "SUSPECT": "yellow", "FAIL": "red"}


def verdict_color(verdict: Verdict) -> str:
    if verdict.status == "INFRA_ERROR":
        return "red"
    return _COLORS.get(verdict.verdict or "", "white")


def _color() -> bool | None:
    """`False` when NO_COLOR carries a value, else `None` for click's default.

    The NO_COLOR convention is "set to any non-empty value", which is what an
    empty-string check gives, and `None` is click's own "decide from the
    stream", so an unset variable leaves tty detection exactly as it was.
    """
    return False if os.environ.get("NO_COLOR") else None


def render_verdict(verdict: Verdict, *, fix_verified: bool | None,
                   cached: bool = False) -> None:
    marker = " (cached)" if cached else ""
    color = verdict_color(verdict)
    if verdict.status == "INFRA_ERROR":
        typer.secho(f"INFRA ERROR: {verdict.infra_reason}{marker}",
                    fg=color, bold=True, color=_color())
    else:
        typer.secho(f"VERDICT {verdict.verdict}{marker}",
                    fg=color, bold=True, color=_color())
    typer.echo(f"score {verdict.suspect_score:.2f}")
    for e in verdict.evidence:
        typer.echo(f"{e.check} · {e.rule} · {e.category} · {e.severity} · "
                   f"{e.location or '-'} · {e.artifact}")
    typer.echo(f"checks: {len(verdict.checks_completed)} completed · "
               f"{len(verdict.not_applicable)} n/a · "
               f"{len(verdict.checks_infra)} infra")
    typer.echo(f"fix_verified: {fix_verified}")
    typer.echo(f"profile {verdict.profile} · isolation {verdict.isolation}")
