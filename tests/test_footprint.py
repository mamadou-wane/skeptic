"""docs/evaluation.md's footprint table, the README's and docs/architecture.md's
figures, and the M7 bar are read against the committed record, the way every
other published figure is bound to the run it came from."""
import importlib.util
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SCRIPTS = REPO_ROOT / "scripts"
RECORD = REPO_ROOT / "evals" / "v1" / "footprint" / "footprint-20260829.json"


def _load(name: str, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


footprint = _load("footprint", "footprint_script")


def _record() -> dict:
    return json.loads(RECORD.read_text())


def _carries(text: str, value: str | float, unit: str = "s") -> bool:
    """The figure with its unit, wherever the sentence around it went."""
    return re.search(rf"\b{value}\s{unit}\b", text) is not None


def test_the_record_is_the_run_the_table_describes():
    record = _record()
    steps, totals = record["steps"], record["totals"]
    # totals are sums of the timed steps, re-derived rather than trusted
    assert totals["clone_to_first_verdict_s"] == round(
        sum(steps[s]["seconds"] for s in ("clone", "install", "cold")), 1)
    assert totals["clone_to_demo_s"] == round(
        sum(steps[s]["seconds"] for s in ("clone", "install", "demo")), 1)
    # the conditions the prose claims
    assert record["build_cache"] == "pruned"
    assert record["task_image_removed"] is True
    assert steps["demo"]["exit"] == 0 and len(steps["demo"]["verdicts"]) == 2
    assert steps["doctor"]["exit"] == 3, "no key on a stranger's path"
    assert steps["cold"]["verdict"] == "VERDICT PASS"
    assert record["base_image"]["pull_timed"] is False
    # the M7 exit criterion, as a test rather than a sentence
    assert totals["clone_to_first_verdict_s"] < 600


def test_evaldoc_footprint_table_cites_the_committed_record():
    record = _record()
    doc = (REPO_ROOT / "docs" / "evaluation.md").read_text()
    start = doc.index("## The lanes")
    section = doc[start:doc.index("\n## CI patch audit", start)]
    for line in footprint.render(record).splitlines():
        if line.startswith("| ") and "---" not in line:
            assert line in section, f"footprint row drifted from the record: {line[:60]}"
    assert record["ref"][:7] in section, "the commit measured is named beside the table"
    assert RECORD.name in section


def test_readme_and_architecture_carry_the_records_own_figures():
    record = _record()
    totals, sizes = record["totals"], record["sizes_bytes"]
    demo_s = f"{totals['clone_to_demo_s']:.0f}"
    first_s = f"{totals['clone_to_first_verdict_s']:.0f}"

    readme = (REPO_ROOT / "README.md").read_text()
    start = readme.index("## Getting started")
    section = readme[start:readme.index("\n## How it works", start)]
    assert _carries(section, demo_s) and _carries(section, first_s)

    arch = (REPO_ROOT / "docs" / "architecture.md").read_text()
    start = arch.index("Footprint anchor")
    para = arch[start:arch.index("\n\n", start)]
    assert _carries(para, demo_s) and _carries(para, first_s)
    files_mb = round(sum(sizes[k] for k in ("checkout", "venv", "workdir")) / 1e6)
    assert _carries(para, files_mb, "MB")
    assert _carries(para, round(sizes["task_image"] / 1e6), "MB")
    assert _carries(para, round(sizes["base_image"] / 1e6), "MB")


def test_render_marks_a_missing_size_rather_than_raising():
    record = _record()
    record["sizes_bytes"]["base_image"] = None
    assert "n/a of image content" in footprint.render(record)
