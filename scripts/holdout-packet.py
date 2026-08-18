#!/usr/bin/env python3
"""Builds one blind-holdout authoring packet for a corpus task.

The packet is everything the holdout author is allowed to see. Its contents
are fixed by the M6 spec's blind-holdout section:

    tree/         the seeded tree at the pinned commit, materialized exactly
                  the way admission does it (`clone_pinned`, `materialize`,
                  then the seed patch, all through `seedcheck._fresh_seeded`)
                  and chmod'd read-only afterwards
    taxonomy.md   the engineering plan's Part 2 table, columns ID / Name /
                  What the patch does / Seed recipe
    task.md       task id, repo and pinned commit, install and test commands,
                  config_files, golden_dirs, problem statement, allowed_paths,
                  and the failing test ids
    seed.diff     the seed patch, already applied in `tree/`

Withheld, and never written here: `notes_private`, the `verification` block,
`evaluation.variants`, `acceptance/`, every non-seed diff under `patches/`,
`skeptic/checks/`, `docs/admission/`, README.md, and the plan's prose below
the taxonomy table (the attempted-violation sentence, the two worked variant
sketches, the known blind spots list). The Control column, both detector
columns and the Tier column are withheld with them: tiers are defined as
detection difficulty, which is calibration signal for the exact thing the
holdout measures. `scripts/holdout-leakcheck.py` is what asserts all of that
against a built packet.

The build is deterministic: byte-identical output on re-run at the same
commit, no timestamps, no absolute paths written into the packet, stable
ordering everywhere. That is what makes the committed digest a provenance
claim rather than a label, since the packets themselves are twelve
materialized upstream trees and are not committed.

The digest written to `packets.yaml` is sha256 over the sorted lines
"<relpath> <file-sha256>\\n" for every file under the packet directory, with
`relpath` POSIX and relative to the packet root (`holdout_common.
packet_sha256`).
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import holdout_common
import yaml
from holdout_common import REPO_ROOT

from skeptic.errors import SkepticInfraError
from skeptic.seedcheck import _fresh_seeded
from skeptic.spec import TaskSpec, find_task
from skeptic.workspace import clone_pinned

PLAN = REPO_ROOT / "docs" / "skeptic-engineering-plan.md"
DEFAULT_PACKETS_YAML = REPO_ROOT / "evals" / "v1" / "holdout" / "packets.yaml"

# The plan's Part 2 table: header, separator, then H1 through H10. Pinned by
# line number because the spec names the range, and located by header text so
# an edit above the table moves the range and fails here instead of silently
# excerpting the wrong twelve lines.
TAXONOMY_HEADER = "| ID | Name | What the patch does | Seed recipe |"
TAXONOMY_LINES = (276, 287)
TAXONOMY_COLUMNS = 8
KEPT_COLUMNS = 4


def taxonomy_excerpt(plan_path: Path = PLAN) -> str:
    """The Part 2 table reduced to its first four columns."""
    lines = plan_path.read_text().splitlines()
    starts = [i for i, line in enumerate(lines, 1) if line.startswith(TAXONOMY_HEADER)]
    if len(starts) != 1:
        raise SkepticInfraError(
            f"expected exactly one taxonomy table header in {plan_path}, found "
            f"{len(starts)} at lines {starts}. The packet excerpts that table "
            f"and nothing else. Next: check the plan's Part 2 heading."
        )
    first = starts[0]
    last = first
    while last < len(lines) and lines[last].startswith("|"):
        last += 1
    if (first, last) != TAXONOMY_LINES:
        raise SkepticInfraError(
            f"the taxonomy table is at lines {first}-{last} of {plan_path}, not "
            f"the pinned {TAXONOMY_LINES[0]}-{TAXONOMY_LINES[1]}. The M6 spec "
            f"names that range, so a moved table means the spec and the plan "
            f"disagree about what a packet shows. Next: reconcile them, then "
            f"update TAXONOMY_LINES."
        )
    rows = []
    for line in lines[first - 1:last]:
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != TAXONOMY_COLUMNS:
            raise SkepticInfraError(
                f"taxonomy row {line!r} has {len(cells)} columns, not "
                f"{TAXONOMY_COLUMNS}. The excerpt keeps the first "
                f"{KEPT_COLUMNS} by position, so a changed column count would "
                f"withhold or expose the wrong one."
            )
        rows.append("| " + " | ".join(cells[:KEPT_COLUMNS]) + " |")
    return "# Patch categories\n\n" + "\n".join(rows) + "\n"


def _bullets(values: list[str], empty: str = "none") -> str:
    return "\n".join(f"- `{value}`" for value in values) if values else f"- {empty}"


def render_task_md(spec: TaskSpec) -> str:
    """The packet's `task.md`, from the spec fields the packet may show."""
    env = spec.environment
    return f"""# {spec.task_id}

## Repository

- url: {spec.repo.url}
- commit: `{spec.repo.commit}`
- python: {spec.repo.python}

## Environment

Install:

{_bullets(env.install)}

Test command:

- `{env.test_cmd}`

Config files:

{_bullets(env.config_files)}

Golden directories:

{_bullets(env.golden_dirs)}

## Problem statement

{spec.builder_input.problem_statement.strip()}

## Allowed paths

{_bullets(spec.builder_input.allowed_paths)}

`allowed_paths` binds the Builder this task evaluates. It does not bind you.

## Seed diff

`seed.diff` beside this file is the patch that introduces the bug. It is
already applied in `tree/`.

## Failing tests

{_bullets(spec.seed.failing_tests)}
"""


def build_packet(
    spec: TaskSpec, packet_dir: Path, repo_cache: Path, plan_path: Path = PLAN
) -> str:
    """Build one packet and return its sha256.

    Any earlier build at `packet_dir` is removed first, write bits restored,
    so a rebuild cannot inherit a stale file and quietly change the digest.
    """
    if spec.seed.bug_patch is None:
        raise SkepticInfraError(
            f"{spec.task_id} declares no seed.bug_patch: the packet shows the "
            f"seeded tree and the seed diff, and neither exists without one."
        )
    holdout_common.rmtree_readonly(packet_dir)
    packet_dir.mkdir(parents=True)
    repo = clone_pinned(spec.repo.url, spec.repo.commit, repo_cache)
    tree = _fresh_seeded(spec, repo, packet_dir / "tree")
    (packet_dir / "taxonomy.md").write_text(taxonomy_excerpt(plan_path))
    (packet_dir / "task.md").write_text(render_task_md(spec))
    shutil.copyfile(Path(spec.seed.bug_patch), packet_dir / "seed.diff")
    _make_readonly(tree)
    return holdout_common.packet_sha256(packet_dir)


def _make_readonly(tree: Path) -> None:
    """Drop every write bit under `tree`, deepest path first.

    The spec calls the materialized copy read-only, so the packet enforces it
    rather than asking the author to respect it. Directories lose their write
    bit too, which is what stops a file being created or removed inside them;
    read and execute bits are untouched, so the tree stays fully readable.
    """
    for path in sorted([tree, *tree.rglob("*")], reverse=True):
        if path.is_symlink():
            continue
        path.chmod(path.stat().st_mode & ~0o222)


def write_packets_yaml(path: Path, task_id: str, digest: str) -> None:
    """Record one packet's digest, leaving the other tasks' entries alone.

    Ruling 7-A: the digests live here rather than in
    `evals/v1/holdout/registry.yaml`, whose `HoldoutVariant` model forbids
    extra keys and cannot gain one without reopening `skeptic/` during the
    verifier revision freeze.
    """
    existing = {}
    if path.is_file():
        existing = (yaml.safe_load(path.read_text()) or {}).get("packets") or {}
    existing[task_id] = digest
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"packets": dict(sorted(existing.items()))},
                                   sort_keys=False))


def main() -> None:
    parser = argparse.ArgumentParser(description="Build one holdout authoring packet.")
    parser.add_argument("--task", required=True, help="Task id (tasks/<id>.yaml).")
    parser.add_argument("--tasks-dir", type=Path, default=Path("tasks"))
    parser.add_argument("--workdir", type=Path, default=Path("workdir"))
    parser.add_argument("--packets-yaml", type=Path, default=DEFAULT_PACKETS_YAML)
    args = parser.parse_args()

    try:
        spec = find_task(args.task, args.tasks_dir)
        workdir = args.workdir.resolve()
        packet_dir = workdir / "holdout" / "packets" / spec.task_id
        digest = build_packet(spec, packet_dir,
                              repo_cache=workdir / spec.task_id / "repo-cache")
    except SkepticInfraError as exc:
        print(f"INFRA ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    write_packets_yaml(args.packets_yaml, spec.task_id, digest)
    print(f"{spec.task_id} {digest}")
    print(f"packet {packet_dir}")
    print(f"Next: `python scripts/holdout-leakcheck.py --packet {packet_dir}`")


if __name__ == "__main__":
    main()
