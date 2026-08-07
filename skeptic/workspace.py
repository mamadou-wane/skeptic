from __future__ import annotations

import hashlib
import os
import subprocess
import tarfile
import tempfile
from pathlib import Path

from skeptic.errors import SkepticInfraError


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=False
    )
    if check and proc.returncode != 0:
        raise SkepticInfraError(
            f"git {' '.join(args)} failed in {cwd} (exit {proc.returncode}):\n"
            f"{proc.stderr[-1500:]}\n"
            f"Skeptic needs git to manage pinned repo checkouts. "
            f"Next: verify git is installed and the repo cache is intact, or "
            f"delete the cache dir and re-run."
        )
    return proc


def clone_pinned(url: str, commit: str, cache_dir: Path) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha256(url.encode()).hexdigest()[:12]
    repo = cache_dir / name
    if not repo.exists():
        proc = subprocess.run(
            ["git", "clone", "--quiet", url, str(repo)],
            capture_output=True, text=True, check=False,
        )
        if proc.returncode != 0:
            raise SkepticInfraError(
                f"git clone failed for {url} (exit {proc.returncode}):\n"
                f"{proc.stderr[-1500:]}\n"
                f"Skeptic caches one clone per repo URL. Next: check the URL and "
                f"your network, then re-run."
            )
    has = _git(repo, "cat-file", "-e", f"{commit}^{{commit}}", check=False)
    if has.returncode != 0:
        _git(repo, "fetch", "--quiet", "origin", check=False)
        has = _git(repo, "cat-file", "-e", f"{commit}^{{commit}}", check=False)
    if has.returncode != 0:
        raise SkepticInfraError(
            f"Pinned commit {commit} not found in {url} (cache {repo}). "
            f"Skeptic only runs against pinned commits so results reproduce. "
            f"Next: fix repo.commit in the task spec, then re-run "
            f"`skeptic seed --task <id> --check`."
        )
    return repo


def materialize(repo_dir: Path, commit: str, dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".tar") as tmp:
        proc = subprocess.run(
            ["git", "-C", str(repo_dir), "archive", "--format=tar", "-o", tmp.name, commit],
            capture_output=True, text=True, check=False,
        )
        if proc.returncode != 0:
            raise SkepticInfraError(
                f"git archive failed for {commit} (exit {proc.returncode}):\n"
                f"{proc.stderr[-1500:]}\nSkeptic needs a clean archive of the "
                f"pinned commit to materialize a gitless workspace. Next: "
                f"delete the repo cache and re-run."
            )
        with tarfile.open(tmp.name) as tar:
            tar.extractall(dest, filter="data")
    assert_no_git(dest)
    return dest


def _git_apply(
    workspace: Path, patch_path: Path
) -> tuple[list[str], subprocess.CompletedProcess] | None:
    """`git apply --check` then `git apply`; the failing step, or None.

    Both callers share the mechanics and neither shares the message: the seed
    path tells the operator to regenerate the patch and re-run `seed --check`,
    and none of that advice fits a candidate diff.
    """
    # Resolve the patch path against the caller's CWD before we run git inside
    # the workspace: patch paths in a task spec are relative to the repo root
    # where skeptic runs, not to the ephemeral workspace we git-apply them in.
    patch_abs = str(patch_path.resolve())
    # The workspace is gitless by design, but it may sit inside an ancestor git
    # repo (e.g. the default `workdir/` under skeptic's own checkout). Without a
    # ceiling, `git apply` discovers that ancestor repo, switches to index-aware
    # mode, and silently *skips* the patch (rc 0, no file change): a no-op seed.
    # Cap the repo search at the workspace's parent so apply runs in plain-file
    # mode regardless of what encloses the workspace.
    apply_env = {**os.environ, "GIT_CEILING_DIRECTORIES": str(workspace.resolve().parent)}
    for args in (["apply", "--check", patch_abs], ["apply", patch_abs]):
        proc = subprocess.run(
            ["git", *args], cwd=workspace, env=apply_env,
            capture_output=True, text=True, check=False,
        )
        if proc.returncode != 0:
            return args, proc
    return None


def apply_patch(workspace: Path, patch_path: Path) -> None:
    failed = _git_apply(workspace, patch_path)
    if failed is None:
        return
    args, proc = failed
    raise SkepticInfraError(
        f"Patch {patch_path.name} does not apply cleanly to the workspace "
        f"(git {args[0]} {args[1] if len(args) > 1 else ''} "
        f"exit {proc.returncode}):\n"
        f"{proc.stderr[-1500:]}\n"
        f"Skeptic requires every variant patch to apply to the seeded state "
        f"(task invariant 3). Next: regenerate the patch against the current "
        f"pinned commit, then re-run `skeptic seed --task <id> --check`."
    )


def apply_candidate(tree: Path, diff: Path) -> None:
    """Apply the extracted candidate diff to a freshly seeded VERIFY tree.

    Same mechanics as `apply_patch`, different advice. BUILD snapshots the
    seeded tree immediately after the seed patch and before the container
    starts, so the diff's a-side is byte-identical to `materialize` plus the
    seed patch, and a diff that fails to apply here is a harness bug or an
    edited file rather than a patch that needs regenerating.
    """
    failed = _git_apply(tree, diff)
    if failed is None:
        return
    args, proc = failed
    raise SkepticInfraError(
        f"Candidate diff {diff.name} does not apply to the freshly seeded tree "
        f"at {tree} (git {' '.join(args[:-1])} exit {proc.returncode}):\n"
        f"{proc.stderr[-1500:]}\n"
        f"VERIFY re-materializes the seeded tree and re-applies the extracted "
        f"diff so the judged tree is one the Builder never touched. That diff "
        f"was taken against this exact state, so a failure here is a harness "
        f"bug or a modified diff file. Next: compare {diff} against the tree "
        f"BUILD ran on, then report the traceback."
    )


def assert_no_git(workspace: Path) -> None:
    hits = list(workspace.rglob(".git"))
    if hits:
        raise SkepticInfraError(
            f"Workspace {workspace} contains {hits[0]}: a workspace must never "
            f"carry .git (the parent commit would leak the pristine fix to the "
            f"Builder). Next: re-materialize via `git archive` (delete the "
            f"workspace and re-run)."
        )


def removed_lines(patch_path: Path, min_chars: int = 12) -> list[str]:
    out: list[str] = []
    for line in patch_path.read_text().splitlines():
        if line.startswith("-") and not line.startswith("---"):
            content = line[1:]
            if len("".join(content.split())) >= min_chars:
                out.append(content)
    return out


def assert_text_absent(workspace: Path, snippets: list[str]) -> None:
    for path in sorted(workspace.rglob("*")):
        if not path.is_file():
            continue
        try:
            text = path.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        for snippet in snippets:
            if snippet.strip() and snippet in text:
                raise SkepticInfraError(
                    f"Pristine text reachable from workspace: {path} contains "
                    f"{snippet.strip()[:60]!r}. The hidden reference must not be "
                    f"recoverable from the seeded tree. Next: adjust the seed "
                    f"patch so replaced lines do not survive verbatim."
                )


def assert_pristine_unreachable(workspace: Path, patch_path: Path, min_chars: int = 12) -> None:
    """Raise SkepticInfraError if any substantive pristine line the seed patch
    removed survives as a COMPLETE line (whitespace-normalized) anywhere in the
    seeded workspace. Whole-line matching (owner decision 2026-07-23): the seed's
    own buggy replacement line is a different complete line, so an additive edit
    (x -> x - 1) does not trip this, while a verbatim leftover copy (backup file,
    commented-out original, docstring) is still caught."""
    pristine = {" ".join(line.split()) for line in removed_lines(patch_path, min_chars)}
    if not pristine:
        return
    for path in sorted(workspace.rglob("*")):
        if not path.is_file():
            continue
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            text = path.read_text(errors="replace")  # scan non-UTF-8 files too
        except OSError:
            continue
        for raw in text.splitlines():
            norm = " ".join(raw.split())
            if norm and norm in pristine:
                raise SkepticInfraError(
                    f"Pristine line reachable in the seeded tree: {path} contains "
                    f"the complete line {norm[:60]!r}, which the seed patch removed. "
                    f"Skeptic needs the pristine reference to be unrecoverable from "
                    f"the seeded tree, or the Builder could copy the fix back. Next: "
                    f"adjust the seed patch so removed lines do not survive verbatim "
                    f"as a complete line elsewhere."
                )
