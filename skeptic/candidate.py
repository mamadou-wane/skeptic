from __future__ import annotations

import fnmatch
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from skeptic.errors import SkepticInfraError

# Runtime residue that must never appear in a candidate: the overlay venv,
# pytest caches, bytecode, editable-install metadata, junit artifacts.
EXCLUDE_NAMES = frozenset({".sv", ".pytest_cache", "__pycache__"})
EXCLUDE_GLOBS = ("*.pyc", "*.egg-info", ".skeptic-junit*")


def _ignored(name: str) -> bool:
    return name in EXCLUDE_NAMES or any(fnmatch.fnmatch(name, g) for g in EXCLUDE_GLOBS)


def snapshot(workspace: Path, dest: Path) -> None:
    """Copy the seeded tree before BUILD so the candidate diff has a baseline."""
    shutil.copytree(
        workspace, dest,
        ignore=lambda _dir, names: [n for n in names if _ignored(n)],
    )


@dataclass(frozen=True)
class CandidateReport:
    diff_path: Path
    changed_files: list[str]
    out_of_scope: list[str]
    is_empty: bool


def extract_candidate(
    baseline: Path, workspace: Path, out_diff: Path, allowed_paths: list[str]
) -> CandidateReport:
    proc = subprocess.run(
        ["git", "diff", "--no-index", "--binary", "--no-renames", "--",
         str(baseline), str(workspace)],
        capture_output=True, text=True, check=False,
    )
    # git diff --no-index exits 0 on identical trees, 1 on differences
    if proc.returncode not in (0, 1):
        raise SkepticInfraError(
            f"git diff --no-index failed (exit {proc.returncode}): "
            f"{proc.stderr[-800:]}\n"
            f"Skeptic extracts the candidate as a diff of the workspace "
            f"against its pre-BUILD snapshot. Next: check both directories "
            f"exist and re-run."
        )
    # Rewrite absolute tree prefixes to workspace-relative paths, dropping
    # excluded entries (git --no-index has no exclude flag of its own).
    # git strips the leading slash from absolute paths in the headers.
    # For robustness, detect which root the b-side carries and slice the
    # correct prefix: compare against str(workspace)[1:] + "/" and
    # str(baseline)[1:] + "/" to handle deletions where the b-side uses
    # the baseline path, additions where it uses workspace, and renames
    # (now delete + add via --no-renames) where paths vary by file.
    lines_out: list[str] = []
    changed: list[str] = []
    keep = True

    workspace_prefix = str(workspace)[1:] + "/"
    baseline_prefix = str(baseline)[1:] + "/"

    for line in proc.stdout.splitlines():
        if line.startswith("diff --git "):
            rel = ""
            if " b/" in line:
                b_part = line.split(" b/", 1)[1]
                if b_part.startswith(workspace_prefix):
                    rel = b_part[len(workspace_prefix):]
                elif b_part.startswith(baseline_prefix):
                    rel = b_part[len(baseline_prefix):]
            keep = bool(rel) and not any(_ignored(p) for p in Path(rel).parts)
            if keep:
                changed.append(rel)
        if keep:
            # Rewrite all combinations of prefixes to relative paths so no
            # absolute path survives in any header line.
            rewritten = (
                line.replace(f"a{baseline}/", "a/")
                    .replace(f"a{workspace}/", "a/")
                    .replace(f"b{baseline}/", "b/")
                    .replace(f"b{workspace}/", "b/")
                    .replace(f"a/{baseline_prefix}", "a/")
                    .replace(f"a/{workspace_prefix}", "a/")
                    .replace(f"b/{baseline_prefix}", "b/")
                    .replace(f"b/{workspace_prefix}", "b/")
            )
            lines_out.append(rewritten)
    text = "\n".join(lines_out) + ("\n" if lines_out else "")
    out_diff.parent.mkdir(parents=True, exist_ok=True)
    out_diff.write_text(text)
    out_of_scope = [
        f for f in changed
        if not any(f == p.rstrip("/") or f.startswith(p.rstrip("/") + "/")
                   for p in allowed_paths)
    ]
    return CandidateReport(
        diff_path=out_diff, changed_files=sorted(changed),
        out_of_scope=sorted(out_of_scope), is_empty=not changed,
    )
