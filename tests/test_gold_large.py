"""Size-matched clean controls: one `gold-large` variant per corpus task.

Issue #33. Every false-positive figure the repo published before these
controls was measured on clean patches of at most 10 lines while the hacked
patches run to 120 (DECISIONS row 241). `gold-large` is a behavior-preserving
refactor of 20 to 100 changed lines that includes the seeded bug's fix, in
every task's clean acceptance path. The band is measured by
`evalkit.changed_lines`, the one counting method for the issue: added and
removed lines inside hunks, headers excluded.
"""
from pathlib import Path

import pytest

from skeptic.evalkit import changed_lines
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
