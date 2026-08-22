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


def test_extract_candidate_defeats_gitattributes_diff_suppression(tmp_path):
    # 2026-07-26 review finding 5: `git diff --no-index` honors an in-tree
    # .gitattributes. A Builder planting `*.py -diff` at the workspace root
    # would otherwise turn every changed .py file into an opaque
    # `GIT binary patch` blob. -c core.attributesFile=/dev/null does NOT
    # defeat this (verified empirically: it only overrides the *global*
    # attributes file, not one committed in the tree), so the diff is taken
    # from copies with .gitattributes/.gitignore stripped. The stripped
    # files must not go invisible: they still show up in changed_files and
    # out_of_scope.
    ws = tmp_path / "ws"
    _seed_tree(ws)
    snapshot(ws, tmp_path / "base")
    (ws / "pkg" / "mod.py").write_text("x = 2\n")
    (ws / ".gitattributes").write_text("*.py -diff\n")

    report = extract_candidate(tmp_path / "base", ws, tmp_path / "candidate.diff",
                               allowed_paths=["pkg/"])

    assert report.changed_files == [".gitattributes", "pkg/mod.py"]
    assert report.out_of_scope == [".gitattributes"]
    text = report.diff_path.read_text()
    assert "GIT binary patch" not in text
    assert "-x = 1" in text and "+x = 2" in text


def test_extract_candidate_handles_dangling_symlink(tmp_path):
    # 2026-07-26 review finding 1: shutil.copytree defaults to
    # symlinks=False, which dereferences a symlink it copies. A dangling
    # symlink (target does not exist) then raises shutil.Error out of
    # extract_candidate, unwinding a paid BUILD run. symlinks=True on both
    # copytree calls copies the link itself instead.
    ws = tmp_path / "ws"
    _seed_tree(ws)
    snapshot(ws, tmp_path / "base")
    (ws / "dangling_link").symlink_to(ws / "does_not_exist")

    report = extract_candidate(tmp_path / "base", ws, tmp_path / "candidate.diff",
                               allowed_paths=["pkg/"])

    assert not report.is_empty
    assert "dangling_link" in report.changed_files
    text = report.diff_path.read_text()
    assert "new file mode 120000" in text
    assert "does_not_exist" in text


def test_extract_candidate_handles_directory_symlink_loop(tmp_path):
    # Same root cause as above: a symlink pointing at its own parent
    # directory makes a dereferencing copy recurse until it hits
    # ELOOP/shutil.Error. symlinks=True copies the link without following it.
    ws = tmp_path / "ws"
    _seed_tree(ws)
    snapshot(ws, tmp_path / "base")
    (ws / "loop_dir").mkdir()
    (ws / "loop_dir" / "self").symlink_to(ws / "loop_dir")

    report = extract_candidate(tmp_path / "base", ws, tmp_path / "candidate.diff",
                               allowed_paths=["pkg/"])

    assert not report.is_empty
    assert "loop_dir/self" in report.changed_files
    text = report.diff_path.read_text()
    assert "new file mode 120000" in text


def test_extract_candidate_does_not_leak_symlink_target_outside_workspace(tmp_path):
    # 2026-07-26 review finding 2: a workspace symlink pointing outside the
    # workspace (e.g. at a host file) is dereferenced by a symlinks=False
    # copy, so the diff carries the host file's content instead of
    # representing the symlink itself. The candidate diff must show the
    # symlink (mode 120000, target path as content), never the target
    # file's bytes.
    ws = tmp_path / "ws"
    _seed_tree(ws)
    snapshot(ws, tmp_path / "base")
    host_file = tmp_path / "host_secret.txt"
    host_file.write_text("TOP SECRET HOST CONTENT sentinel-98234\n")
    (ws / "leak_link").symlink_to(host_file)

    report = extract_candidate(tmp_path / "base", ws, tmp_path / "candidate.diff",
                               allowed_paths=["pkg/"])

    text = report.diff_path.read_text()
    assert "TOP SECRET HOST CONTENT" not in text
    assert "new file mode 120000" in text
    assert "leak_link" in report.changed_files


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


def test_extract_candidate_preserves_crlf_so_the_diff_still_applies(tmp_path):
    """A CRLF tree's candidate diff has to apply back to that tree.

    `git diff` renders a CRLF file's lines with the CR as part of the line
    content, so a patch that drops it no longer matches the file it came from.
    Found in the field: `EinDev/watchman-pairing-assistant#40` is a CRLF repo,
    and its candidate diff failed `git apply --check` against the very tree it
    had just been extracted from, surfacing as an INFRA_ERROR from the
    `verify --diff` lane the GitHub Action wraps (`DECISIONS.md` row 227).
    The cause was newline translation on the way out of `git diff`, twice:
    `text=True` decodes with universal newlines, and `splitlines()` treats a
    lone CR as a terminator too.
    """
    import subprocess

    base = tmp_path / "base"
    ws = tmp_path / "ws"
    for root, body in ((base, "a\r\nb\r\nc\r\n"), (ws, "a\r\nB\r\nc\r\n")):
        (root / "pkg").mkdir(parents=True)
        (root / "pkg" / "mod.py").write_bytes(body.encode())

    out = tmp_path / "candidate.diff"
    report = extract_candidate(base, ws, out, ["pkg/"])
    assert report.changed_files == ["pkg/mod.py"]

    raw = out.read_bytes()
    assert b"\r\n" in raw, "the CR was stripped out of the patch body"

    # The real assertion: the patch applies to the tree it was taken from.
    applied = subprocess.run(
        ["git", "apply", "--check", str(out)],
        cwd=base, capture_output=True, text=True, check=False,
    )
    assert applied.returncode == 0, applied.stderr


def test_extract_candidate_leaves_an_lf_tree_alone(tmp_path):
    """The CRLF fix must not start emitting CRs for an ordinary LF tree: every
    corpus task is LF, and a spurious CR would fail to apply the same way."""
    import subprocess

    base = tmp_path / "base"
    ws = tmp_path / "ws"
    for root, body in ((base, "a\nb\nc\n"), (ws, "a\nB\nc\n")):
        (root / "pkg").mkdir(parents=True)
        (root / "pkg" / "mod.py").write_bytes(body.encode())

    out = tmp_path / "candidate.diff"
    extract_candidate(base, ws, out, ["pkg/"])
    assert b"\r" not in out.read_bytes()

    applied = subprocess.run(
        ["git", "apply", "--check", str(out)],
        cwd=base, capture_output=True, text=True, check=False,
    )
    assert applied.returncode == 0, applied.stderr
