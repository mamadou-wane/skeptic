"""Terminal rendering for a verdict, shared by `verify` and `demo`.

Split out of verify's tail, which also wrote verdict.json: the demo renders
real verdicts through the real aggregator but owns no run directory, so it
needs the echoing half and not the persisting half. Color goes through
typer.secho, which is click's, which already honors NO_COLOR and a non-tty
stdout; that is the whole reason the plan added no color dependency.
"""
from __future__ import annotations

import typer

from skeptic.checks.evidence import Verdict

_COLORS = {"PASS": "green", "SUSPECT": "yellow", "FAIL": "red"}


def verdict_color(verdict: Verdict) -> str:
    if verdict.status == "INFRA_ERROR":
        return "red"
    return _COLORS.get(verdict.verdict or "", "white")


def render_verdict(verdict: Verdict, *, fix_verified: bool | None,
                   cached: bool = False) -> None:
    marker = " (cached)" if cached else ""
    color = verdict_color(verdict)
    if verdict.status == "INFRA_ERROR":
        typer.secho(f"INFRA ERROR: {verdict.infra_reason}{marker}",
                    fg=color, bold=True)
    else:
        typer.secho(f"VERDICT {verdict.verdict}{marker}", fg=color, bold=True)
    typer.echo(f"score {verdict.suspect_score:.2f}")
    for e in verdict.evidence:
        typer.echo(f"{e.check} · {e.rule} · {e.category} · {e.severity} · "
                   f"{e.location or '-'} · {e.artifact}")
    typer.echo(f"checks: {len(verdict.checks_completed)} completed · "
               f"{len(verdict.not_applicable)} n/a · "
               f"{len(verdict.checks_infra)} infra")
    typer.echo(f"fix_verified: {fix_verified}")
    typer.echo(f"profile {verdict.profile} · isolation {verdict.isolation}")
