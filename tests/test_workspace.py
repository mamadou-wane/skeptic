import subprocess
from pathlib import Path

import pytest

from skeptic.errors import SkepticInfraError
from skeptic.workspace import (
    apply_patch,
    assert_no_git,
    assert_text_absent,
    clone_pinned,
    materialize,
    removed_lines,
)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture()
def source_repo(tmp_path):
    """A local git repo standing in for a pinned upstream."""
    repo = tmp_path / "upstream"
    repo.mkdir()
    (repo / "mod.py").write_text("def add(a, b):\n    return a + b\n")
    (repo / "README.md").write_text("fixture\n")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
    commit = _git(repo, "rev-parse", "HEAD").strip()
    return repo, commit


def test_clone_and_materialize_produce_gitless_workspace(source_repo, tmp_path):
    repo, commit = source_repo
    cache = tmp_path / "cache"
    cached = clone_pinned(str(repo), commit, cache)
    ws = tmp_path / "ws"
    materialize(cached, commit, ws)
    assert (ws / "mod.py").read_text().startswith("def add")
    assert not (ws / ".git").exists()
    assert_no_git(ws)  # does not raise


def test_clone_pinned_unknown_commit_is_infra_error(source_repo, tmp_path):
    repo, _ = source_repo
    with pytest.raises(SkepticInfraError, match="0" * 7):
        clone_pinned(str(repo), "0" * 40, tmp_path / "cache")


def test_clone_pinned_reuses_cache(source_repo, tmp_path):
    repo, commit = source_repo
    cache = tmp_path / "cache"
    first = clone_pinned(str(repo), commit, cache)
    second = clone_pinned(str(repo), commit, cache)
    assert first == second


def test_apply_patch_and_removed_lines(source_repo, tmp_path):
    repo, commit = source_repo
    # Craft a seed patch: replace correct add() with an off-by-one.
    _git(repo, "checkout", "-q", commit)
    (repo / "mod.py").write_text("def add(a, b):\n    return a + b + 1\n")
    patch_text = _git(repo, "diff")
    _git(repo, "checkout", "-q", "--", "mod.py")
    patch = tmp_path / "seed.diff"
    patch.write_text(patch_text)

    ws = tmp_path / "ws"
    materialize(clone_pinned(str(repo), commit, tmp_path / "cache"), commit, ws)
    apply_patch(ws, patch)
    assert "a + b + 1" in (ws / "mod.py").read_text()

    pristine = removed_lines(patch)
    assert pristine == ["    return a + b"]
    with pytest.raises(SkepticInfraError, match="mod.py"):
        assert_text_absent(ws, ["a + b + 1"])  # present -> must raise
    assert_text_absent(ws, ["return a + b\n    # not present anywhere"])  # absent -> ok


def test_apply_patch_conflict_is_infra_error(source_repo, tmp_path):
    repo, commit = source_repo
    ws = tmp_path / "ws"
    materialize(clone_pinned(str(repo), commit, tmp_path / "cache"), commit, ws)
    bad = tmp_path / "bad.diff"
    bad.write_text(
        "--- a/mod.py\n+++ b/mod.py\n@@ -1,2 +1,2 @@\n-def NOT_THERE():\n+def x():\n     pass\n"
    )
    with pytest.raises(SkepticInfraError, match="apply"):
        apply_patch(ws, bad)


def test_assert_no_git_catches_planted_git_dir(tmp_path):
    ws = tmp_path / "ws"
    (ws / ".git").mkdir(parents=True)
    with pytest.raises(SkepticInfraError, match=r"\.git"):
        assert_no_git(ws)
