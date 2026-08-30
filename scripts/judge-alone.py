"""Re-sample the judge on the twelve gold patches, N pre-committed runs.

    python scripts/judge-alone.py --runs 3 --out evals/v1/judge-alone

Each run gets a fresh `--workdir`, so the VERIFY stage cache cannot replay a
judge call: every one of the twelve `verify --task <t> --variant gold
--profile paid` verdicts is a new sample. What lands per run, under
`<out>/run-<n>/<task>/`, is the verdict, the judge's report and io, and the
trace; a run-level JSON records the per-task flag, the run's paid spend from
the traces, and the verifier revision. `render` prints the per-run counts
the docs carry, one line per run and never a pooled rate: the patches are the
same twelve each time, so what this measures is the judge's stochastic
stability on this clean set, not its false-positive rate on clean patches it
has not seen.

The number of runs is an argument on purpose and is set once, before the
first run, by the person paying. The script does not look at a run's result
before starting the next.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

TASKS = ["click-0001", "click-0002", "click-0003", "click-0004", "click-0005", "click-0006",
         "rich-0001", "rich-0002", "rich-0003", "rich-0004", "rich-0005", "rich-0006"]
KEEP = ("verdict.json", "t2_judge.json", "t2_judge_io.json")


def spend(trace: Path) -> float:
    usd = 0.0
    for line in trace.read_text().splitlines():
        event = json.loads(line)
        if event.get("event") == "llm_call":
            usd += (event.get("usage") or {}).get("usd", 0.0)
    return usd


def one_run(n: int, out: Path, workdir: Path, skeptic: str) -> dict:
    from skeptic.orchestrator import verifier_revision

    run_dir = out / f"run-{n}"
    run_dir.mkdir(parents=True, exist_ok=True)
    record: dict = {"run": n, "started": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "verifier_revision": verifier_revision(), "tasks": {}, "usd": 0.0}
    for task in TASKS:
        proc = subprocess.run(
            [skeptic, "verify", "--task", task, "--variant", "gold", "--profile", "paid",
             "--yes", "--workdir", str(workdir)],
            capture_output=True, text=True, check=False)
        src = workdir / task / "verify" / "gold"
        artifacts = src / "collect" / "artifacts"
        dst = run_dir / task
        dst.mkdir(parents=True, exist_ok=True)
        for name in KEEP:
            if (artifacts / name).is_file():
                shutil.copy(artifacts / name, dst / name)
        if (src / "trace.jsonl").is_file():
            shutil.copy(src / "trace.jsonl", dst / "trace.jsonl")
        judge = artifacts / "t2_judge.json"
        flagged = json.loads(judge.read_text())["report"]["flagged"] if judge.is_file() else None
        verdict = next((line for line in proc.stdout.splitlines() if line.startswith("VERDICT")), None)
        usd = spend(src / "trace.jsonl") if (src / "trace.jsonl").is_file() else 0.0
        record["tasks"][task] = {"judge_flagged": flagged, "verdict": verdict,
                                 "exit": proc.returncode, "usd": round(usd, 4)}
        record["usd"] = round(record["usd"] + usd, 4)
        print(f"run {n} {task}: judge_flagged={flagged} {verdict} ${usd:.4f}", flush=True)
    flags = [t["judge_flagged"] for t in record["tasks"].values()]
    record["fp_gold"] = sum(1 for f in flags if f)
    record["n_gold"] = sum(1 for f in flags if f is not None)
    (run_dir / "run.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    return record


def render(records: list[dict]) -> str:
    """One line per run, counts only: the docs carry these, never a pooled rate."""
    lines = ["| run | judge flagged, of the twelve gold | which | verifier revision | spend |",
             "|---|---|---|---|---|"]
    for r in records:
        which = ", ".join(t for t, v in r["tasks"].items() if v["judge_flagged"]) or "none"
        lines.append(f"| {r['run']} | {r['fp_gold']} of {r['n_gold']} | {which} | "
                     f"`{r['verifier_revision']}` | ${r['usd']:.4f} |")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--runs", type=int, required=True, help="pre-committed count, set once")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--workdir-root", type=Path, default=Path("workdir") / "judge-alone")
    ap.add_argument("--skeptic", default=str(Path(sys.executable).parent / "skeptic"))
    args = ap.parse_args()
    records = []
    for n in range(1, args.runs + 1):
        workdir = args.workdir_root / f"run-{n}"
        if workdir.exists():
            print(f"{workdir} exists; a fresh workdir is the whole point", file=sys.stderr)
            return 3
        records.append(one_run(n, args.out, workdir, args.skeptic))
    (args.out / "runs.json").write_text(json.dumps(records, indent=2, sort_keys=True) + "\n")
    print(render(records))
    return 0


if __name__ == "__main__":
    sys.exit(main())
