"""Ten paid sweeps, reported per run: the script, the doc and the README.

Issue #33 (b), DECISIONS row 243's pre-registration. Every cell the script
prints is recomputed here from the raw `verdict.json` files without
`evalkit`, so a fold change and a doc edit fail apart from each other; the
evaluation doc must quote the script verbatim, and the README's spread
sentence must derive from the same runs.
"""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "paid-repeats.py"
EVALUATION_DOC = ROOT / "docs" / "evaluation.md"
README = ROOT / "README.md"
SECTION = "## Paid repeats, ten sweeps"
NEXT_SECTION = "## v1.0.1 integrity hotfix revalidation"


def _load_script():
    spec = importlib.util.spec_from_file_location("paid_repeats", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _raw(run: str) -> dict[tuple[str, str], str | None]:
    """{(task, variant): verdict} straight off the snapshot files."""
    out = {}
    for verdict_file in sorted((ROOT / "evals" / "v1" / "runs" / run).glob("*/*/verdict.json")):
        data = json.loads(verdict_file.read_text())
        out[(data["task_id"], data["variant"])] = data["verdict"]
    return out


def _detection(raw, clean_splits) -> tuple[str, str]:
    hacked = [v for (_, variant), v in raw.items()
              if variant not in clean_splits and v is not None]
    lenient = sum(v in ("SUSPECT", "FAIL") for v in hacked)
    strict = sum(v == "FAIL" for v in hacked)
    return f"{lenient}/{len(hacked)}", f"{strict}/{len(hacked)}"


def _fp(raw, split) -> str:
    clean = [v for (_, variant), v in raw.items() if variant == split and v is not None]
    return f"{sum(v != 'PASS' for v in clean)}/{len(clean)}"


def _table_rows(text: str) -> list[list[str]]:
    rows = []
    for line in text.splitlines():
        if line.startswith("| ") and not line.startswith("|---") and not line.startswith("| sweep"):
            rows.append([c.strip() for c in line.strip("|").split("|")])
    return rows


def _section(text: str) -> str:
    start = text.index(SECTION)
    return text[start:text.index(NEXT_SECTION, start)]


@pytest.fixture(scope="module")
def script():
    return _load_script()


def test_ten_runs_share_one_harness_and_none_is_infra(script):
    verifiers, collectors = set(), set()
    for label, run in script.EVAL_A + script.HOLDOUT:
        manifest = json.loads((script.RUNS_DIR / run / "manifest.json").read_text())
        verifiers.add(manifest["verifier_revision"])
        collectors.add(str(manifest["collector_version"]))
        raw = _raw(run)
        expected = 65 if label.startswith("a") else 11
        assert len(raw) == expected, (label, run, len(raw))
        assert all(v is not None for v in raw.values()), (label, run)
    assert len(verifiers) == 1 and collectors == {"4"}, (verifiers, collectors)
    assert verifiers.pop() in script.render_provenance()


def test_eval_a_table_matches_the_raw_verdicts(script):
    rows = _table_rows(script.render_eval_a())
    assert [r[0] for r in rows] == [label for label, _ in script.EVAL_A]
    for label, run in script.EVAL_A:
        raw = _raw(run)
        lenient, strict = _detection(raw, script.CLEAN_SPLITS)
        row = next(r for r in rows if r[0] == label)
        assert row[1:8] == [run, "0", lenient, strict, _fp(raw, "gold"),
                            _fp(raw, "gold-prime"), _fp(raw, "gold-large")], row
        assert row[8].startswith("$") and float(row[8][1:]) > 0


def test_holdout_table_matches_the_raw_verdicts(script):
    rows = _table_rows(script.render_holdout())
    assert [r[0] for r in rows] == [label for label, _ in script.HOLDOUT]
    for label, run in script.HOLDOUT:
        raw = _raw(run)
        lenient, strict = _detection(raw, script.CLEAN_SPLITS)
        row = next(r for r in rows if r[0] == label)
        assert row[1:5] == [run, "0", lenient, strict], row


@pytest.mark.parametrize("corpus", ["EVAL_A", "HOLDOUT"])
def test_stability_table_lists_exactly_the_rows_that_moved(script, corpus):
    sweeps = getattr(script, corpus)
    registry = None
    if corpus == "HOLDOUT":
        from skeptic.evalkit import load_holdout_registry
        registry = load_holdout_registry(script.REGISTRY)
    per_row: dict[tuple[str, str], list[str]] = {}
    for _, run in sweeps:
        for key, verdict in _raw(run).items():
            per_row.setdefault(key, []).append(verdict or "INFRA")
    moved = {key: draws for key, draws in per_row.items() if len(set(draws)) > 1}
    text = script.render_stability(sweeps, registry)
    rows = _table_rows(text)
    assert {(r[0], r[1]): r[2:] for r in rows if r[0] != "task"} == \
        {key: draws for key, draws in moved.items()}
    assert text.endswith(f"rows with the same verdict in all {len(sweeps)} draws: "
                         f"{len(per_row) - len(moved)} of {len(per_row)}")


def test_evaluation_doc_quotes_the_script_verbatim(script):
    from skeptic.evalkit import load_holdout_registry
    section = _section(EVALUATION_DOC.read_text())
    assert script.render_provenance() in section
    assert script.render_eval_a() in section
    assert script.render_holdout() in section
    assert script.render_stability(script.EVAL_A) in section
    assert script.render_stability(script.HOLDOUT, load_holdout_registry(script.REGISTRY)) in section
    for _, run in script.EVAL_A + script.HOLDOUT:
        assert f"`evals/v1/runs/{run}/`" in section, run


def test_readme_states_the_spread_from_the_runs(script):
    """The README carries sweep 1 as the headline and one sentence on the
    spread; both derive from the runs, never from a literal."""
    readme = " ".join(README.read_text().split())
    a_rows = _table_rows(script.render_eval_a())
    h_rows = _table_rows(script.render_holdout())
    a_lenient = sorted(int(r[3].split("/")[0]) for r in a_rows)
    h_lenient = sorted(int(r[3].split("/")[0]) for r in h_rows)
    assert f"lenient read {a_lenient[0]} to {a_lenient[-1]} of 29 on the dev set" in readme
    assert f"{h_lenient[0]} to {h_lenient[-1]} of 11 on the holdout" in readme
    assert len({r[4] for r in a_rows}) == 1, "strict moved between draws; the README sentence assumes it did not"
    assert f"strict {a_rows[0][4]} and {h_rows[0][4]} in every draw" in readme
    for split in ("gold", "gold-prime", "gold-large"):
        assert all(r[5 + ("gold", "gold-prime", "gold-large").index(split)] == "0/12" for r in a_rows), split
    assert "0/12 on all three clean splits in every draw" in readme
