"""Size-matched clean controls: one `gold-large` variant per corpus task.

Issue #33. Every false-positive figure the repo published before these
controls was measured on clean patches of at most 10 lines while the hacked
patches run to 120 (DECISIONS row 241). `gold-large` is a behavior-preserving
refactor of 20 to 100 changed lines that includes the seeded bug's fix, in
every task's clean acceptance path. The band is measured by
`evalkit.changed_lines`, the one counting method for the issue: added and
removed lines inside hunks, headers excluded.
"""
import importlib.util
import json
from pathlib import Path

import pytest

from skeptic.evalkit import changed_lines, load_rows
from skeptic.spec import list_tasks

ROOT = Path(__file__).resolve().parent.parent
TASKS = list_tasks(ROOT / "tasks")
BAND = (20, 100)


def _control(spec):
    return next((v for v in spec.evaluation.variants if v.id == "gold-large"), None)


def _changed_files(patch: Path) -> list[str]:
    """Paths named by the `---`/`+++` headers, `/dev/null` excluded, so a plain
    unified diff and `git diff` output read the same."""
    files: list[str] = []
    for line in patch.read_text().splitlines():
        if line.startswith(("--- ", "+++ ")):
            name = line[4:].split("\t")[0].strip()
            if name == "/dev/null":
                continue
            name = name[2:] if name.startswith(("a/", "b/")) else name
            if name not in files:
                files.append(name)
    return files


@pytest.mark.parametrize("spec", TASKS, ids=lambda s: s.task_id)
def test_gold_large_is_a_clean_variant_on_the_acceptance_path(spec):
    control = _control(spec)
    assert control is not None, f"{spec.task_id} declares no gold-large variant"
    assert control.label == "clean", spec.task_id
    assert control.patch == f"patches/{spec.task_id}-gold-large.diff", spec.task_id
    assert "gold-large" in spec.acceptance_suite.must_pass_on, spec.task_id


@pytest.mark.parametrize("spec", TASKS, ids=lambda s: s.task_id)
def test_gold_large_is_20_to_100_changed_lines(spec):
    patch = ROOT / f"patches/{spec.task_id}-gold-large.diff"
    assert patch.exists(), patch
    n = changed_lines(patch)
    assert BAND[0] <= n <= BAND[1], (spec.task_id, n)


@pytest.mark.parametrize("spec", TASKS, ids=lambda s: s.task_id)
def test_gold_large_touches_only_source_dirs(spec):
    """No test, conftest, config or golden edits: the control is a source refactor."""
    patch = ROOT / f"patches/{spec.task_id}-gold-large.diff"
    files = _changed_files(patch)
    assert files, spec.task_id
    src_dirs = tuple(spec.environment.src_dirs)
    assert all(f.startswith(src_dirs) for f in files), (spec.task_id, files)


# --- scripts/gold-large-controls.py: the table docs/evaluation.md quotes ----

SCRIPT = ROOT / "scripts" / "gold-large-controls.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("gold_large_controls", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_run(tmp_path: Path, rows: dict[tuple[str, str], tuple[str, float]]) -> Path:
    run = tmp_path / "run"
    for (task, variant), (verdict, score) in rows.items():
        snapshot = run / task / variant
        snapshot.mkdir(parents=True)
        (snapshot / "verdict.json").write_text(json.dumps(
            {"verdict": verdict, "suspect_score": score, "evidence": []}))
        (snapshot / "meta.json").write_text("{}")
    return run


def test_controls_table_reads_size_from_the_patch_and_verdict_from_the_run(tmp_path):
    mod = _load_script()
    run = _fake_run(tmp_path, {
        ("click-0001", "gold-large"): ("PASS", 0.0),
        ("click-0001", "gold"): ("PASS", 0.0),
        ("rich-0002", "gold-large"): ("SUSPECT", 1.25),
    })
    text = mod.render_controls(run, ROOT / "tasks")
    assert "| click-0001 | " in text
    assert "| 28 | PASS | 0.00 |" in text
    assert "| 98 | SUSPECT | 1.25 |" in text
    assert "changed lines, min/median/max: 28/63/98" in text


def test_split_table_keeps_each_clean_split_on_its_own_denominator(tmp_path):
    mod = _load_script()
    run = _fake_run(tmp_path, {
        ("click-0001", "gold"): ("PASS", 0.0),
        ("click-0001", "gold-prime"): ("PASS", 0.0),
        ("click-0001", "gold-large"): ("SUSPECT", 1.0),
        ("click-0001", "h5"): ("SUSPECT", 1.25),
        ("click-0001", "h1"): ("FAIL", 0.0),
    })
    text = mod.render_splits(run, ROOT / "tasks")
    assert "| gold | 1 | 1 | 0 | 0 | 0 |" in text
    assert "| gold-prime | 1 | 1 | 0 | 0 | 0 |" in text
    assert "| gold-large | 1 | 0 | 1 | 0 | 0 |" in text
    assert "| hacked | 2 | 0 | 1 | 1 | 0 |" in text


# --- drift: the docs quote the committed sweep, never a hand-typed figure --

EVALUATION_DOC = ROOT / "docs" / "evaluation.md"
README = ROOT / "README.md"


def _section(text: str, start: str, end: str) -> str:
    begin = text.index(start)
    return text[begin:text.index(end, begin)]


def _sweep_rows():
    mod = _load_script()
    return mod, [r for r in load_rows(mod.SWEEP_RUN, mod.TASKS_DIR)]


def test_evaluation_doc_quotes_the_controls_script_verbatim():
    mod = _load_script()
    section = _section(EVALUATION_DOC.read_text(), "## Size-matched clean controls",
                       "## v1.0.1 integrity hotfix revalidation")
    assert f"`{mod.SWEEP_RUN.relative_to(ROOT)}/`" in section
    assert mod.render_controls(mod.SWEEP_RUN, mod.TASKS_DIR) in section
    assert mod.render_splits(mod.SWEEP_RUN, mod.TASKS_DIR) in section


def test_evaluation_doc_prose_matches_the_sweep():
    _, rows = _sweep_rows()
    hacked = [r for r in rows if r.label == "hacked"]
    lenient = sum(r.verdict in ("SUSPECT", "FAIL") for r in hacked)
    strict = sum(r.verdict == "FAIL" for r in hacked)
    controls = [r for r in rows if r.variant == "gold-large"]
    flagged = sum(r.verdict != "PASS" for r in controls)
    section = _section(EVALUATION_DOC.read_text(), "## Size-matched clean controls",
                       "## v1.0.1 integrity hotfix revalidation")
    assert f"{lenient}/{len(hacked)} lenient and {strict}/{len(hacked)} strict" in section
    assert f"{flagged}/{len(controls)}" in section
    assert sum(r.verdict is None for r in rows) == 0
    assert "0 INFRA" in section


def test_readme_states_the_control_figures_from_the_sweep():
    _, rows = _sweep_rows()
    controls = [r for r in rows if r.variant == "gold-large"]
    sizes = [changed_lines(ROOT / f"patches/{r.task_id}-gold-large.diff") for r in controls]
    flagged = sum(r.verdict != "PASS" for r in controls)
    readme = README.read_text()
    assert f"{min(sizes)} to {max(sizes)} changed lines" in readme
    assert f"{flagged}/{len(controls)} under the deterministic profile" in readme
