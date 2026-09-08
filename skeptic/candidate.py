from __future__ import annotations

import fnmatch
import hashlib
import os
import re
import shutil
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from skeptic.errors import SkepticInfraError

# Runtime residue that must never appear in a candidate: the overlay venv,
# pytest caches, bytecode, editable-install metadata, junit artifacts.
EXCLUDE_NAMES = frozenset({".sv", ".pytest_cache", "__pycache__"})
EXCLUDE_GLOBS = ("*.pyc", "*.egg-info", ".skeptic-junit*")

# Unchanged control files are omitted only from temporary diff views. Any
# candidate change to one is refused before extraction; it is never dropped
# from an admitted patch. The original baseline retains unchanged controls.
DIFF_CONTROL_NAMES = frozenset({".gitattributes", ".gitignore"})


def _ignored(name: str) -> bool:
    return name.casefold() in EXCLUDE_NAMES or any(
        fnmatch.fnmatch(name.casefold(), g) for g in EXCLUDE_GLOBS)


def snapshot(workspace: Path, dest: Path) -> None:
    """Copy the seeded tree before BUILD so the candidate diff has a baseline."""
    shutil.copytree(
        workspace, dest, symlinks=True,
        ignore=lambda _dir, names: [n for n in names if _ignored(n)],
    )


def _diff_safe_copy(src: Path, dest: Path) -> None:
    """Neutralize in-tree diff controls without dereferencing links."""
    shutil.copytree(
        src, dest, symlinks=True,
        ignore=lambda _dir, names: [
            n for n in names if _ignored(n) or n.casefold() in DIFF_CONTROL_NAMES
        ],
    )


def patch_git_env() -> dict[str, str]:
    """Patch transport does not inherit repository selection or diff drivers."""
    env = {k: v for k, v in os.environ.items()
           if k not in {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
                        "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_CONFIG_PARAMETERS", "GIT_ATTR_SOURCE"}
           and not k.startswith("GIT_CONFIG_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    return env


def _refuse(detail: str) -> None:
    raise SkepticInfraError(
        f"Candidate change is unsupported: {detail}. "
        "Next: submit a complete patch using supported repository-relative files."
    )


def _supported_path(name: str, *, submitted: bool = False) -> None:
    if (not name or name.startswith("/") or PureWindowsPath(name).drive
            or any(part.casefold() in ("", ".", "..", ".git") for part in name.split("/"))
            or not re.fullmatch(r"[A-Za-z0-9_./+@=-]+", name)):
        _refuse(f"path {name!r} cannot be represented by the diff and coverage readers")
    if submitted and any(_ignored(part) for part in name.split("/")):
        _refuse(f"explicit patch touches excluded runtime path {name!r}")
    if submitted and Path(name).name.casefold() in DIFF_CONTROL_NAMES:
        _refuse(f"diff-control file {name!r} changed")


def validate_submitted_patch(diff: Path) -> None:
    """Refuse unsupported input before git applies it or runtime filters hide it."""
    try:
        data = diff.read_bytes()
    except OSError as exc:
        raise SkepticInfraError(f"Cannot read candidate patch {diff}: {exc}. "
                                "Next: supply a readable patch file.") from exc
    headers = 0
    in_hunk = False
    for line in data.split(b"\n"):
        if line.startswith(b"diff --git "):
            headers += 1
            in_hunk = False
            parts = line[11:].split(b" ")
            if len(parts) != 2:
                _refuse("quoted or whitespace-containing Git filename")
            for raw in parts:
                if raw[:2] not in (b"a/", b"b/"):
                    _refuse("quoted or unrecognized Git filename")
                try:
                    name = raw[2:].decode("ascii")
                except UnicodeDecodeError:
                    _refuse("non-ASCII Git filename")
                _supported_path(name, submitted=True)
        elif line.startswith(b"@@ "):
            in_hunk = True
        elif (not in_hunk and line.startswith((b"index ", b"old mode ", b"new mode ",
                                              b"new file mode ", b"deleted file mode "))
              and line.endswith(b" 160000")):
            _refuse("gitlinks/submodules have no supported execution contract")
    if not headers:
        _refuse("patch dialect has no Git file headers")


def tree_inventory(root: Path, *, ignore_runtime: bool = True) -> dict[str, tuple[str, str]]:
    """Git-relevant content and modes, read without following symlinks."""
    entries: dict[str, tuple[str, str]] = {}

    def walk(directory: Path) -> None:
        for entry in sorted(os.scandir(directory), key=lambda e: e.name):
            if ignore_runtime and _ignored(entry.name):
                continue
            path = Path(entry.path)
            name = path.relative_to(root).as_posix()
            if entry.name.casefold() == ".git":
                _refuse(f"nested repository metadata at {name!r}")
            mode = entry.stat(follow_symlinks=False).st_mode
            if stat.S_ISLNK(mode):
                entries[name] = ("120000", hashlib.sha256(os.fsencode(os.readlink(path))).hexdigest())
            elif stat.S_ISDIR(mode):
                walk(path)
            elif stat.S_ISREG(mode):
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(65536), b""):
                        digest.update(chunk)
                entries[name] = ("100755" if mode & stat.S_IXUSR else "100644", digest.hexdigest())
            else:
                _refuse(f"special file at {name!r}")
    walk(root)
    return entries


def validate_execution_tree(tree: Path) -> None:
    """Canonical readers must not follow a candidate link into host authority."""
    root = tree.resolve(strict=True)
    for name, (mode, _) in tree_inventory(root, ignore_runtime=False).items():
        if mode != "120000":
            continue
        try:
            target = (root / name).resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise SkepticInfraError(
                f"Candidate symlink {name!r} is dangling or unresolvable. "
                "Next: use contained, resolvable links before evaluation.") from exc
        if target == root or root not in target.parents:
            _refuse(f"symlink {name!r} escapes the execution tree")


@dataclass(frozen=True)
class CandidateReport:
    diff_path: Path
    changed_files: list[str]
    out_of_scope: list[str]
    is_empty: bool


def extract_candidate(
    baseline: Path, workspace: Path, out_diff: Path, allowed_paths: list[str]
) -> CandidateReport:
    """Produce a complete supported patch and verify it by independent tree comparison."""
    before, after = tree_inventory(baseline), tree_inventory(workspace)
    changed = sorted(name for name in before.keys() | after.keys()
                     if before.get(name) != after.get(name))
    for name in changed:
        _supported_path(name, submitted=True)
    with tempfile.TemporaryDirectory(prefix="skeptic-candidate-") as tmp:
        root = Path(tmp)
        _diff_safe_copy(baseline, root / "baseline")
        _diff_safe_copy(workspace, root / "workspace")
        command = ["git", "-c", "core.quotePath=true", "diff", "--no-index", "--no-renames",
                   "--no-ext-diff", "--no-textconv"]

        def diff(args):
            result = subprocess.run(command + args + ["--", "baseline", "workspace"],
                                    cwd=root, env=patch_git_env(), capture_output=True, check=False)
            if result.returncode not in (0, 1):
                raise SkepticInfraError(
                    f"git diff failed: {result.stderr.decode(errors='replace')[-800:]}. "
                    "Next: inspect the candidate and baseline trees.")
            return result.stdout

        # NUL-delimited inventory is independent of textual header rewriting.
        records = diff(["--raw", "-z"]).split(b"\0")
        raw_paths: set[str] = set()
        for index in range(0, len(records) - 1, 2):
            header, raw = records[index:index + 2]
            if not header.startswith(b":") or header.split()[-1] not in (b"M", b"A", b"D", b"T"):
                _refuse("unexpected raw Git change record")
            name = raw.decode("ascii").split("/", 1)[1]
            raw_paths.add(name)
        if raw_paths != set(changed):
            _refuse("Git change inventory disagrees with the candidate tree")

        normalized: list[bytes] = []
        headers: set[str] = set()
        in_hunk = False
        for line in diff(["--binary"]).split(b"\n"):
            if line.startswith(b"diff --git "):
                in_hunk = False
                parts = line[11:].split(b" ")
                if len(parts) != 2:
                    _refuse("unexpected Git file header")
                headers.add(parts[1].decode("ascii").split("/", 2)[2])
            if line.startswith(b"@@ "):
                in_hunk = True
            if not in_hunk and line.startswith((b"diff --git ", b"--- ", b"+++ ")):
                for side in (b"a", b"b"):
                    for source in (b"baseline", b"workspace"):
                        line = line.replace(side + b"/" + source + b"/", side + b"/")
            normalized.append(line)
        if headers != raw_paths:
            _refuse("normalized patch omits a changed path")
        data = b"\n".join(normalized)
        if changed:
            from skeptic.workspace import _git_apply
            replay = root / "replayed"
            snapshot(baseline, replay)
            temporary_patch = root / "candidate.diff"
            temporary_patch.write_bytes(data)
            if _git_apply(replay, temporary_patch) is not None or tree_inventory(replay) != after:
                _refuse("normalized patch does not reproduce the complete candidate tree")
    out_diff.parent.mkdir(parents=True, exist_ok=True)
    out_diff.write_bytes(data)
    out_of_scope = [f for f in changed if not any(
        f == p.rstrip("/") or f.startswith(p.rstrip("/") + "/") for p in allowed_paths)]
    return CandidateReport(out_diff, changed, sorted(out_of_scope), not changed)
