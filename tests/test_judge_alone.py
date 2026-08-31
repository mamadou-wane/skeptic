"""The judge re-sample: three pre-committed draws on the twelve gold patches,
reported per run beside the two committed observations, and every figure the
docs carry derived from the records."""
import importlib.util
import json
import sys
from pathlib import Path

from skeptic.evalkit import load_rows

REPO_ROOT = Path(__file__).parent.parent
SCRIPTS = REPO_ROOT / "scripts"
RUNS = REPO_ROOT / "evals" / "v1" / "judge-alone" / "runs.json"
PRIOR = {"2026-08-16": "evals/v1/runs/eval-20260816-225027",
         "2026-08-22": "evals/v1/runs/eval-20260822-195147"}


def _load(name: str, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


judge_alone = _load("judge-alone", "judge_alone_script")


def _records() -> list[dict]:
    return json.loads(RUNS.read_text())


def _prior_counts() -> dict[str, int]:
    out = {}
    for date, run in PRIOR.items():
        rows = load_rows(REPO_ROOT / run, REPO_ROOT / "tasks")
        gold = [r for r in rows if r.variant == "gold"]
        assert len(gold) == 12 and all(r.judge_flagged is not None for r in gold)
        out[date] = sum(1 for r in gold if r.judge_flagged)
    return out


def test_three_draws_were_pre_committed_and_each_count_derives_from_its_flags():
    records = _records()
    assert [r["run"] for r in records] == [1, 2, 3]
    for r in records:
        flags = [t["judge_flagged"] for t in r["tasks"].values()]
        assert len(flags) == 12 and None not in flags
        assert r["fp_gold"] == sum(flags) and r["n_gold"] == 12
        assert all(t["verdict"] == "VERDICT PASS" for t in r["tasks"].values())


def test_the_three_draws_saw_one_request_and_reproduced_its_flags():
    """Temperature 0, byte-identical request per task across the three runs,
    identical flags back: the draws measure reproducibility at one revision,
    which is what the doc says they measure."""
    records = _records()
    tasks = list(records[0]["tasks"])
    for task in tasks:
        requests = [json.loads((REPO_ROOT / "evals" / "v1" / "judge-alone" / f"run-{n}" / task
                                / "t2_judge_io.json").read_text())["request"] for n in (1, 2, 3)]
        assert requests[0]["temperature"] == 0
        assert requests[0] == requests[1] == requests[2], task
        assert len({r["tasks"][task]["judge_flagged"] for r in records}) == 1, task


def test_evaldoc_and_readme_carry_the_per_run_counts_and_no_pooled_rate():
    records = _records()
    prior = _prior_counts()
    doc = (REPO_ROOT / "docs" / "evaluation.md").read_text()
    section = doc[doc.index("## Eval A"):doc.index("\n## The blind holdout")]
    for line in judge_alone.render(records).splitlines():
        if line.startswith("| ") and "---" not in line:
            assert line in section, f"judge-alone row drifted from the record: {line[:60]}"
    assert f"scored {prior['2026-08-16']} of 12 on gold in the 2026-08-16 run" in section
    assert f"{prior['2026-08-22']} of 12 in the 2026-08-22 collector-1 run" in section

    sequence = [prior["2026-08-16"], prior["2026-08-22"]] + [r["fp_gold"] for r in records]
    phrase = ", ".join(str(n) for n in sequence[:-1]) + f" and {sequence[-1]} of 12"
    readme = (REPO_ROOT / "README.md").read_text()
    assert phrase in readme
    # per-run counts, never a pooled denominator over the five draws
    assert "of 60" not in section and "of 60" not in readme
