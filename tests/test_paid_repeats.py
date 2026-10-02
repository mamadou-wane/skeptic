"""Ten paid sweeps, reported per run: the script and the doc.

Issue #33 (b), DECISIONS row 243's pre-registration. Every cell the script
prints is recomputed here from the raw `verdict.json` files without
`evalkit`, so a fold change and a doc edit fail apart from each other; the
evaluation doc must quote the script verbatim.
"""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "paid-repeats.py"
EVALUATION_DOC = ROOT / "docs" / "evaluation.md"
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


HEADER_CELLS = {"sweep", "task"}


def _table_rows(text: str) -> list[list[str]]:
    rows = []
    for line in text.splitlines():
        if line.startswith("| ") and not line.startswith("|---"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if cells[0] not in HEADER_CELLS:
                rows.append(cells)
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
    assert {(r[0], r[1]): r[2:] for r in rows} == moved
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
    assert "Every run named in the tables is under `evals/v1/runs/`" in " ".join(section.split())
    for _, run in script.EVAL_A + script.HOLDOUT:
        assert run in section, run


def _collapsed(path: Path) -> str:
    return " ".join(path.read_text().split())


def _eval_rows(script, sweeps, registry=None):
    from skeptic.evalkit import load_rows
    return {label: load_rows(script.RUNS_DIR / run, script.TASKS_DIR, registry=registry)
            for label, run in sweeps}


def test_doc_prose_states_the_bar_the_spread_and_the_spend_from_the_runs(script):
    """The 85 percent bar's own pass/fail sentence, the spread, the spend
    ranges and the totals in the Eval A note and the Status paragraph all
    derive from the runs."""
    from skeptic.evalkit import detection, load_holdout_registry
    registry = load_holdout_registry(script.REGISTRY)
    a = _eval_rows(script, script.EVAL_A)
    h = _eval_rows(script, script.HOLDOUT, registry)
    a_len = {k: detection(v)[0] for k, v in a.items()}
    h_len = {k: detection(v)[0] for k, v in h.items()}
    assert all(n / 29 >= 0.85 for n in a_len.values())
    under = sorted(k for k, n in h_len.items() if n / 11 < 0.85)
    assert under == ["h3", "h5"] and {h_len[k] for k in under} == {9}
    doc = _collapsed(EVALUATION_DOC)
    assert ("Every development sweep met the pre-registered 85 percent bar. Three "
            "holdout sweeps met it; h3 and h5 did not, each at 9/11 (81.8 percent), on "
            "the same two sampled rows. The bar stands and the shortfall is published "
            "with it") in doc
    assert f"Lenient detection ranged from {min(a_len.values())}–{max(a_len.values())}/29 on " \
           f"development and {min(h_len.values())}–{max(h_len.values())}/11 on the holdout" in doc
    spend_a = [sum(r.usd for r in v) for v in a.values()]
    spend_h = [sum(r.usd for r in v) for v in h.values()]
    assert f"${min(spend_a):.2f}–${max(spend_a):.2f} per development sweep and " \
           f"${min(spend_h):.2f}–${max(spend_h):.2f} per holdout sweep" in doc
    total = sum(spend_a) + sum(spend_h)
    assert f"Total spend was ${total:.4f} of the $30 cap" in doc
    pairs = sum(len(v) for v in a.values()) + sum(len(v) for v in h.values())
    assert f"All {pairs} evaluated pairs completed without infrastructure errors" in doc
    a1, h1 = a["a1"], h["h1"]
    from skeptic.evalkit import false_positives
    fp = false_positives(a1)
    assert all(fp[s][0] == 0 for s in script.CLEAN_SPLITS)
    assert (f"{detection(a1)[0]}/29 lenient, {detection(a1, strict=True)[0]}/29 strict, "
            "and 0/12 in each clean group") in doc
    assert f"{detection(h1)[0]}/11 lenient and {detection(h1, strict=True)[0]}/11 strict" in doc
    assert "yielded " + ", ".join(str(h_len[k]) for k, _ in script.HOLDOUT[:-1]) \
           + f", and {h_len[script.HOLDOUT[-1][0]]} detections of 11" in doc


def test_doc_accounts_for_every_unstable_pass_draw(script):
    """Every PASS draw on a moving row is either an empty battery
    (`advtest_zero_trusted`) or named as a miss with tests in hand."""
    from skeptic.evalkit import load_holdout_registry
    registry = load_holdout_registry(script.REGISTRY)
    passes, empty, exceptions = 0, 0, []
    for sweeps, reg in ((script.EVAL_A, None), (script.HOLDOUT, registry)):
        per_row = {}
        for label, rows in _eval_rows(script, sweeps, reg).items():
            for r in rows:
                per_row.setdefault((r.task_id, r.variant), []).append((label, r))
        for key, draws in per_row.items():
            if len({r.verdict for _, r in draws}) < 2:
                continue
            for label, r in draws:
                if r.verdict != "PASS":
                    continue
                passes += 1
                if any(e.rule == "advtest_zero_trusted" for e in r.evidence):
                    empty += 1
                else:
                    exceptions.append((label, key))
    doc = _collapsed(EVALUATION_DOC)
    assert (f"received `PASS` in {passes} draws between them. Of those passes, "
            f"{empty} carried `advtest_zero_trusted`") in doc
    assert exceptions == [("h1", ("rich-0006", "holdout-h5"))]
    assert ("The remaining pass was rich-0006's holdout H5 in h1, with two trusted "
            "tests and zero divergences") in doc
