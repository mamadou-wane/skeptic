from __future__ import annotations

import fnmatch
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from skeptic.errors import SkepticInfraError

# Runtime residue that must never appear in a candidate: the overlay venv,
# pytest caches, bytecode, editable-install metadata, junit artifacts.
EXCLUDE_NAMES = frozenset({".sv", ".pytest_cache", "__pycache__"})
EXCLUDE_GLOBS = ("*.pyc", "*.egg-info", ".skeptic-junit*")

# git diff --no-index honors an in-tree .gitattributes: a Builder can write
# one line ("*.py -diff") and every changed .py file renders as an opaque
# `GIT binary patch` instead of a readable hunk. -c core.attributesFile
# points at the *global* attributes file and does nothing for one committed
# in a tree (verified empirically 2026-07-26), so the diff is taken from
# copies with these files removed instead. They must not go invisible: see
# _control_file_changes below.
DIFF_CONTROL_NAMES = frozenset({".gitattributes", ".gitignore"})


def _ignored(name: str) -> bool:
    return name in EXCLUDE_NAMES or any(fnmatch.fnmatch(name, g) for g in EXCLUDE_GLOBS)


def snapshot(workspace: Path, dest: Path) -> None:
    """Copy the seeded tree before BUILD so the candidate diff has a baseline."""
    shutil.copytree(
        workspace, dest, symlinks=True,
        ignore=lambda _dir, names: [n for n in names if _ignored(n)],
    )


def _diff_safe_copy(src: Path, dest: Path) -> None:
    """Copy `src` to `dest`, dropping runtime residue and diff-control files.

    Used only for the tree that gets handed to `git diff --no-index`: a
    .gitattributes or .gitignore anywhere in that tree can change how the
    diff renders (or, with a nonstandard core.excludesFile, what git
    considers). Neither file is part of the candidate under judgment, and
    their own changes are reported separately (_control_file_changes).

    symlinks=True on both this copy and snapshot()'s: shutil.copytree
    defaults to symlinks=False, which dereferences every symlink it copies.
    A dangling symlink then raises shutil.Error, a directory symlink loop
    raises after recursing, and a symlink pointing outside the workspace
    copies the target's content in instead of the link itself (2026-07-26
    review findings 1 and 2). git diff --no-index records a symlink as a
    mode-120000 entry showing the link target, never the target's content,
    which is the behavior this restores. Both calls need it: if only one
    preserved symlinks, a symlink present in the pristine tree would be
    dereferenced on one side and not the other, producing a phantom hunk.
    """
    shutil.copytree(
        src, dest, symlinks=True,
        ignore=lambda _dir, names: [
            n for n in names if _ignored(n) or n in DIFF_CONTROL_NAMES
        ],
    )


def _control_file_changes(baseline: Path, workspace: Path) -> list[str]:
    """Return diff-control files (.gitattributes/.gitignore) that the
    Builder added, removed, or edited, at any depth in the tree.

    These are stripped out of the copy that gets diffed (_diff_safe_copy),
    so they need a direct byte comparison against the real baseline and
    workspace to stay visible instead of disappearing from the report.
    """
    found: set[str] = set()
    for root in (baseline, workspace):
        for p in root.rglob("*"):
            rel = p.relative_to(root)
            if p.is_file() and p.name in DIFF_CONTROL_NAMES and not any(
                _ignored(part) for part in rel.parts
            ):
                found.add(str(rel))
    changed = []
    for rel in sorted(found):
        b_path, w_path = baseline / rel, workspace / rel
        b_bytes = b_path.read_bytes() if b_path.is_file() else None
        w_bytes = w_path.read_bytes() if w_path.is_file() else None
        if b_bytes != w_bytes:
            changed.append(rel)
    return changed


@dataclass(frozen=True)
class CandidateReport:
    diff_path: Path
    changed_files: list[str]
    out_of_scope: list[str]
    is_empty: bool


def extract_candidate(
    baseline: Path, workspace: Path, out_diff: Path, allowed_paths: list[str]
) -> CandidateReport:
    # Diffed from copies with .gitattributes/.gitignore removed (see
    # _diff_safe_copy): a Builder-planted .gitattributes with `*.py -diff`
    # would otherwise make git render every changed .py file as an opaque
    # `GIT binary patch` instead of a readable hunk.
    with tempfile.TemporaryDirectory(prefix="skeptic-candidate-") as tmp:
        clean_baseline = Path(tmp) / "baseline"
        clean_workspace = Path(tmp) / "workspace"
        _diff_safe_copy(baseline, clean_baseline)
        _diff_safe_copy(workspace, clean_workspace)
        proc = subprocess.run(
            ["git", "diff", "--no-index", "--binary", "--no-renames", "--",
             str(clean_baseline), str(clean_workspace)],
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
        # correct prefix: compare against str(clean_workspace)[1:] + "/" and
        # str(clean_baseline)[1:] + "/" to handle deletions where the b-side
        # uses the baseline path, additions where it uses workspace, and
        # renames (now delete + add via --no-renames) where paths vary by
        # file.
        lines_out: list[str] = []
        changed: list[str] = []
        keep = True

        workspace_prefix = str(clean_workspace)[1:] + "/"
        baseline_prefix = str(clean_baseline)[1:] + "/"

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
                    line.replace(f"a{clean_baseline}/", "a/")
                        .replace(f"a{clean_workspace}/", "a/")
                        .replace(f"b{clean_baseline}/", "b/")
                        .replace(f"b{clean_workspace}/", "b/")
                        .replace(f"a/{baseline_prefix}", "a/")
                        .replace(f"a/{workspace_prefix}", "a/")
                        .replace(f"b/{baseline_prefix}", "b/")
                        .replace(f"b/{workspace_prefix}", "b/")
                )
                lines_out.append(rewritten)
    # Diff-control files are stripped from the copies above (so they can
    # never corrupt the diff of other files), which also strips them from
    # `changed`. Fold their own changes back in by direct comparison so a
    # Builder-planted or edited .gitattributes/.gitignore is still reported,
    # never silently invisible (2026-07-26 review finding 5).
    changed = sorted(set(changed) | set(_control_file_changes(baseline, workspace)))
    text = "\n".join(lines_out) + ("\n" if lines_out else "")
    out_diff.parent.mkdir(parents=True, exist_ok=True)
    out_diff.write_text(text)
    out_of_scope = [
        f for f in changed
        if not any(f == p.rstrip("/") or f.startswith(p.rstrip("/") + "/")
                   for p in allowed_paths)
    ]
    return CandidateReport(
        diff_path=out_diff, changed_files=changed,
        out_of_scope=sorted(out_of_scope), is_empty=not changed,
    )
