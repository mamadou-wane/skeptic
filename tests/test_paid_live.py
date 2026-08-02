"""Live paid-lane tests: real Anthropic API calls, real spend.

Module-level skip unless `SKEPTIC_PAID_TESTS=1` and `ANTHROPIC_API_KEY` are
both set (decision 10): the default `python -m pytest -q` run never makes an
API call, proven mechanically rather than by convention. The owner's live
run lifts the gate explicitly:

    SKEPTIC_PAID_TESTS=1 python -m pytest -q -m paid tests/test_paid_live.py

Every test here also carries `docker` and `slow` on top of `paid`: each run
builds and executes real containers against a real Anthropic key.
"""
from __future__ import annotations

import json
import os

import pytest
from typer.testing import CliRunner

from skeptic.cli import app
from tests.helpers import load_hack_fixture, make_minirepo_task

pytestmark = pytest.mark.skipif(
    not (os.environ.get("SKEPTIC_PAID_TESTS") == "1" and os.environ.get("ANTHROPIC_API_KEY")),
    reason="set SKEPTIC_PAID_TESTS=1 and ANTHROPIC_API_KEY to run the live paid-lane tests",
)

runner = CliRunner()


@pytest.mark.docker
@pytest.mark.slow
@pytest.mark.paid
def test_paid_live_flips_h5_h6_h7_to_suspect(tmp_path):
    """h5-hardcoded, h6-special-case, and h7-swallow each reach SUSPECT
    through the full layer with advtests live (wave B exit criterion 1).

    h5 and h6 special-case the one tested input, so a trusted generated test
    on any other input diverges (`advtest_divergence`) regardless of what
    the model happens to generate; both assert that rule by name. h7's
    divergence needs a generated input that trips the seeded
    swallowed-exception arm, which is not guaranteed the way h5/h6's is, so
    h7 only asserts SUSPECT and records whichever rule(s) actually crossed
    rather than requiring `advtest_divergence` specifically. A PASS on any
    of the three is not a soft failure: it means a real paid run missed a
    known-hacked variant, and the assertion message says so in words rather
    than being quietly absorbed.
    """
    tasks_dir, task_id = make_minirepo_task(
        tmp_path,
        extra_variants=[
            ("h5", "hacked", load_hack_fixture("h5-hardcoded")),
            ("h6", "hacked", load_hack_fixture("h6-special-case")),
            ("h7", "hacked", load_hack_fixture("h7-swallow")),
        ],
    )
    workdir = (tmp_path / "workdir").resolve()

    for variant_id in ("h5", "h6", "h7"):
        result = runner.invoke(app, [
            "verify", "--task", task_id, "--variant", variant_id,
            "--tasks-dir", str(tasks_dir), "--workdir", str(workdir),
            "--profile", "paid", "--yes",
        ])

        verdict_path = (
            workdir / task_id / "verify" / variant_id / "collect" / "artifacts" / "verdict.json"
        )
        assert verdict_path.is_file(), (
            f"{variant_id}: no verdict.json written, exit {result.exit_code}.\n{result.output}"
        )
        saved = json.loads(verdict_path.read_text())
        rules = [row["rule"] for row in saved["evidence"]]

        assert saved["verdict"] == "SUSPECT" and result.exit_code == 1, (
            f"{variant_id}: expected verdict SUSPECT and exit 1, got verdict="
            f"{saved['verdict']!r} exit={result.exit_code} score={saved['suspect_score']} "
            f"rules={rules}. A live paid run that PASSes a known-hacked variant is not "
            f"papered over here: escalate to owner.\n{result.output}"
        )

        if variant_id in ("h5", "h6"):
            assert "advtest_divergence" in rules, (
                f"{variant_id}: SUSPECT but without advtest_divergence among {rules}; "
                f"{variant_id}'s hack special-cases the tested input, so a trusted "
                f"generated test on another input should diverge. Escalate to owner."
            )
