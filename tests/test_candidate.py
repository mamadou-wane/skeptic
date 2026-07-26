from pathlib import Path

from skeptic.candidate import extract_candidate, snapshot


def _seed_tree(root: Path) -> None:
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "mod.py").write_text("x = 1\n")
    (root / "tests").mkdir()
    (root / "tests" / "test_mod.py").write_text("def test(): pass\n")


def test_snapshot_copies_tree_without_junk(tmp_path):
    ws = tmp_path / "ws"
    _seed_tree(ws)
    (ws / ".pytest_cache").mkdir()
    (ws / "pkg" / "__pycache__").mkdir()
    snapshot(ws, tmp_path / "base")
    assert (tmp_path / "base" / "pkg" / "mod.py").read_text() == "x = 1\n"
    assert not (tmp_path / "base" / ".pytest_cache").exists()
    assert not (tmp_path / "base" / "pkg" / "__pycache__").exists()


def test_extract_candidate_diffs_and_scopes(tmp_path):
    ws = tmp_path / "ws"
    _seed_tree(ws)
    snapshot(ws, tmp_path / "base")
    (ws / "pkg" / "mod.py").write_text("x = 2\n")
    (ws / "tests" / "test_mod.py").write_text("def test(): assert True\n")
    (ws / ".sv").mkdir()
    (ws / ".sv" / "junk.py").write_text("ignored\n")
    report = extract_candidate(tmp_path / "base", ws, tmp_path / "candidate.diff",
                               allowed_paths=["pkg/"])
    assert not report.is_empty
    assert report.changed_files == ["pkg/mod.py", "tests/test_mod.py"]
    assert report.out_of_scope == ["tests/test_mod.py"]
    text = report.diff_path.read_text()
    assert "-x = 1" in text and "+x = 2" in text
    assert "--- a/pkg/mod.py" in text
    assert "+++ b/pkg/mod.py" in text
    assert ".sv" not in text


def test_extract_candidate_empty_when_untouched(tmp_path):
    ws = tmp_path / "ws"
    _seed_tree(ws)
    snapshot(ws, tmp_path / "base")
    report = extract_candidate(tmp_path / "base", ws, tmp_path / "candidate.diff",
                               allowed_paths=["pkg/"])
    assert report.is_empty and report.changed_files == []


def test_extract_candidate_diff_applies_to_a_baseline_copy(tmp_path):
    # M3 re-applies the candidate to a fresh seeded tree; a diff that does
    # not git-apply is useless downstream, so appliability is the contract.
    import os
    import subprocess

    ws = tmp_path / "ws"
    _seed_tree(ws)
    snapshot(ws, tmp_path / "base")
    (ws / "pkg" / "mod.py").write_text("x = 2\n")
    report = extract_candidate(tmp_path / "base", ws, tmp_path / "c.diff",
                               allowed_paths=["pkg/"])
    check = tmp_path / "check"
    snapshot(tmp_path / "base", check)
    proc = subprocess.run(
        ["git", "apply", str(report.diff_path)], cwd=check,
        env={**os.environ, "GIT_CEILING_DIRECTORIES": str(check.parent)},
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert (check / "pkg" / "mod.py").read_text() == "x = 2\n"


def test_extract_candidate_handles_rename(tmp_path):
    # --no-renames turns a rename into a delete plus add, which applies
    # cleanly and reports both old and new paths.
    import os
    import subprocess

    ws = tmp_path / "ws"
    _seed_tree(ws)
    snapshot(ws, tmp_path / "base")

    # Rename a file.
    old_path = ws / "pkg" / "mod.py"
    new_path = ws / "pkg" / "newmod.py"
    old_path.rename(new_path)

    report = extract_candidate(tmp_path / "base", ws, tmp_path / "candidate.diff",
                               allowed_paths=["pkg/"])

    assert not report.is_empty
    # With --no-renames, this becomes a delete plus add.
    assert "pkg/mod.py" in report.changed_files
    assert "pkg/newmod.py" in report.changed_files

    # Verify the diff applies cleanly.
    check = tmp_path / "check"
    snapshot(tmp_path / "base", check)
    proc = subprocess.run(
        ["git", "apply", str(report.diff_path)], cwd=check,
        env={**os.environ, "GIT_CEILING_DIRECTORIES": str(check.parent)},
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert not (check / "pkg" / "mod.py").exists()
    assert (check / "pkg" / "newmod.py").exists()

    # Verify no absolute paths in diff.
    diff_text = report.diff_path.read_text()
    assert str(tmp_path) not in diff_text
    assert str(ws) not in diff_text


def test_extract_candidate_handles_deletion_in_scope(tmp_path):
    # Deleted files in scope are tracked with correct rel and included in
    # changed_files, not out_of_scope, and the diff applies cleanly.
    import os
    import subprocess

    ws = tmp_path / "ws"
    _seed_tree(ws)
    snapshot(ws, tmp_path / "base")

    # Delete a file in scope.
    (ws / "pkg" / "mod.py").unlink()

    report = extract_candidate(tmp_path / "base", ws, tmp_path / "candidate.diff",
                               allowed_paths=["pkg/"])

    assert not report.is_empty
    assert report.changed_files == ["pkg/mod.py"]
    assert report.out_of_scope == []

    # Verify the diff applies cleanly.
    check = tmp_path / "check"
    snapshot(tmp_path / "base", check)
    proc = subprocess.run(
        ["git", "apply", str(report.diff_path)], cwd=check,
        env={**os.environ, "GIT_CEILING_DIRECTORIES": str(check.parent)},
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert not (check / "pkg" / "mod.py").exists()

    # Verify no absolute paths in diff.
    diff_text = report.diff_path.read_text()
    assert str(tmp_path) not in diff_text


def test_extract_candidate_handles_deletion_out_of_scope(tmp_path):
    # Deleted files outside allowed_paths land in out_of_scope.
    ws = tmp_path / "ws"
    _seed_tree(ws)
    snapshot(ws, tmp_path / "base")

    # Delete a file out of scope.
    (ws / "tests" / "test_mod.py").unlink()

    report = extract_candidate(tmp_path / "base", ws, tmp_path / "candidate.diff",
                               allowed_paths=["pkg/"])

    assert not report.is_empty
    assert report.changed_files == ["tests/test_mod.py"]
    assert report.out_of_scope == ["tests/test_mod.py"]

    # Verify no absolute paths in diff.
    diff_text = report.diff_path.read_text()
    assert str(tmp_path) not in diff_text


def test_extract_candidate_handles_binary_change(tmp_path):
    # Binary file changes are handled with --binary flag and apply cleanly.
    import os
    import subprocess

    ws = tmp_path / "ws"
    _seed_tree(ws)

    # Create a binary file in the baseline.
    (ws / "pkg" / "data.bin").write_bytes(b"\x00\x01\x02\x03")

    snapshot(ws, tmp_path / "base")

    # Modify the binary file in the workspace.
    (ws / "pkg" / "data.bin").write_bytes(b"\x04\x05\x06\x07")

    report = extract_candidate(tmp_path / "base", ws, tmp_path / "candidate.diff",
                               allowed_paths=["pkg/"])

    assert not report.is_empty
    assert report.changed_files == ["pkg/data.bin"]

    # Verify the diff applies cleanly.
    check = tmp_path / "check"
    snapshot(tmp_path / "base", check)
    proc = subprocess.run(
        ["git", "apply", str(report.diff_path)], cwd=check,
        env={**os.environ, "GIT_CEILING_DIRECTORIES": str(check.parent)},
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert (check / "pkg" / "data.bin").read_bytes() == b"\x04\x05\x06\x07"

    # Verify no absolute paths in diff.
    diff_text = report.diff_path.read_text()
    assert str(tmp_path) not in diff_text
