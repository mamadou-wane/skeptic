"""`skeptic verify --diff`: the audit path that reads no task spec.

The guard matrix and the inference units are the specification of the mode.
Every refusal here has to land before docker, because the whole point of a
pre-flight is that a caller with a typo'd ref or an unsupported backend hears
about it in under a second instead of after an image build.

The two docker-marked tests at the bottom are the mode's actual claim: a
patch against a local clone of a pytest-based Python package reaches a real verdict with zero task
knowledge, and a deleted test file is caught there the same way the corpus
catches H1.
"""
import json
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path

import pytest
from typer.testing import CliRunner

from skeptic import cli
from skeptic.cli import app
from skeptic.diffmode import (
    DEFAULT_INSTALL,
    DEFAULT_TEST_CMD,
    assert_python_project,
    assert_supported_backend,
    backend_of,
    infer_environment,
    synthesize_spec,
)
from skeptic.errors import SkepticInfraError
from skeptic.sandbox import DockerDiagnosis
from tests.helpers import FIXTURE

runner = CliRunner()

_DIAG_DOWN = DockerDiagnosis("unreachable", "test")


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), "-c", "user.email=t@t", "-c", "user.name=t", *args],
        check=True, capture_output=True, text=True,
    ).stdout


def make_clone(tmp_path: Path, name: str = "minirepo") -> Path:
    """A real one-commit git clone of the bundled minirepo fixture.

    The fixture is the only repo in the tree that is small enough to install
    and run inside a container in seconds, and `verify --diff` needs a repo
    with git history rather than the corpus's pinned remotes.
    """
    repo = tmp_path / name
    shutil.copytree(FIXTURE, repo)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    return repo


def author_diff(repo: Path, files: Mapping[str, str | None], out: Path) -> Path:
    """Write `files` into `repo`, capture the patch, restore the clone.

    Staged before diffing (`git diff` alone is blind to a new file) and
    cleaned afterwards, so the clone is back at its base commit and the
    audited patch lives outside it, which is exactly the caller's posture:
    a checkout at the base plus a patch file.
    """
    for rel, content in files.items():
        target = repo / rel
        if content is None:
            target.unlink()
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    _git(repo, "add", "-A")
    patch = _git(repo, "diff", "--cached")
    _git(repo, "reset", "-q")
    _git(repo, "checkout", "-q", "--", ".")
    _git(repo, "clean", "-fdq")
    out.write_text(patch)
    return out


_FLAT_CLAMP = "    return max(lo, min(hi, value))"
_BRANCHY_CLAMP = """\
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value"""


def branchy_clamp(repo: Path) -> str:
    """The benign candidate: `clamp` rewritten as explicit branches.

    Every new statement is exercised by the fixture's own
    `test_clamp_bounds`, so patch coverage has a full denominator to score.
    Built by replacing one line in the file rather than by restating the
    module here, which keeps this a clamp-only diff if the fixture grows.
    """
    text = (repo / "minirepo.py").read_text()
    assert _FLAT_CLAMP in text, text
    return text.replace(_FLAT_CLAMP, _BRANCHY_CLAMP)


# ---------------------------------------------------------------- guards

def test_diff_and_task_together_refused(tmp_path):
    """The two modes read different worlds: one resolves a corpus yaml, the
    other synthesizes a spec. Picking silently would hide which one ran."""
    patch = tmp_path / "p.diff"
    patch.write_text("--- a/x\n+++ b/x\n")
    result = runner.invoke(app, ["verify", "--task", "click-0001",
                                 "--diff", str(patch), "--repo", str(tmp_path)])
    assert result.exit_code == 3, result.output
    assert "--diff" in result.output and "--task" in result.output
    assert "Next:" in result.output


def test_neither_diff_nor_task_names_both_modes(tmp_path):
    """--task stopped being a required typer option in this PR, so the
    missing-argument error is now this refusal and it has to name both
    modes rather than only the one the reader forgot."""
    result = runner.invoke(app, ["verify", "--workdir", str(tmp_path)])
    assert result.exit_code == 3, result.output
    assert "--task" in result.output and "--diff" in result.output
    assert "Next:" in result.output


def test_diff_without_repo_refused(tmp_path):
    patch = tmp_path / "p.diff"
    patch.write_text("--- a/x\n+++ b/x\n")
    result = runner.invoke(app, ["verify", "--diff", str(patch)])
    assert result.exit_code == 3, result.output
    assert "--repo" in result.output
    assert "Next:" in result.output


@pytest.mark.parametrize("flag,value", [("--variant", "gold"),
                                        ("--candidate-diff", "c.diff")])
def test_diff_with_corpus_only_flags_refused(tmp_path, flag, value):
    """Both flags name a corpus task's trees. Neither exists in diff mode."""
    patch = tmp_path / "p.diff"
    patch.write_text("--- a/x\n+++ b/x\n")
    result = runner.invoke(app, ["verify", "--diff", str(patch),
                                 "--repo", str(tmp_path), flag, value])
    assert result.exit_code == 3, result.output
    assert flag in result.output
    assert "Next:" in result.output


def test_diff_with_paid_profile_refused(tmp_path):
    """M6 ships the diff lane deterministic-only (spec decision 2): the paid
    checks' behavior in diff posture is unmeasured, and a refusal is the
    only honest answer to a request to spend money on it."""
    patch = tmp_path / "p.diff"
    patch.write_text("--- a/x\n+++ b/x\n")
    result = runner.invoke(app, ["verify", "--diff", str(patch),
                                 "--repo", str(tmp_path), "--profile", "paid"])
    assert result.exit_code == 3, result.output
    assert "deterministic" in result.output
    assert "Next:" in result.output


def test_repo_without_git_refused(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    patch = tmp_path / "p.diff"
    patch.write_text("--- a/x\n+++ b/x\n")
    result = runner.invoke(app, ["verify", "--diff", str(patch), "--repo", str(plain)])
    assert result.exit_code == 3, result.output
    assert ".git" in result.output
    assert "Next:" in result.output


def test_unresolvable_base_names_the_ref_and_the_repo(tmp_path):
    repo = make_clone(tmp_path)
    patch = author_diff(repo, {"minirepo.py": branchy_clamp(repo)}, tmp_path / "p.diff")
    result = runner.invoke(app, ["verify", "--diff", str(patch), "--repo", str(repo),
                                 "--base", "no-such-ref"])
    assert result.exit_code == 3, result.output
    assert "no-such-ref" in result.output
    assert str(repo) in result.output
    assert "Next:" in result.output


def test_missing_diff_file_refused(tmp_path):
    repo = make_clone(tmp_path)
    result = runner.invoke(app, ["verify", "--diff", str(tmp_path / "gone.diff"),
                                 "--repo", str(repo)])
    assert result.exit_code == 3, result.output
    assert "gone.diff" in result.output
    assert "Next:" in result.output


def test_empty_diff_file_refused_before_docker(tmp_path):
    """`git apply` exits 128 on a patch-free file, so an empty diff would
    otherwise surface as an infra failure minutes into a docker run."""
    repo = make_clone(tmp_path)
    patch = tmp_path / "empty.diff"
    patch.write_text("")
    result = runner.invoke(app, ["verify", "--diff", str(patch), "--repo", str(repo)])
    assert result.exit_code == 3, result.output
    assert "empty" in result.output
    assert "Next:" in result.output


# ------------------------------------------------------------- inference

def test_testpaths_from_pyproject(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["suite", "extra"]\n')
    env, lines = infer_environment(tmp_path, DEFAULT_INSTALL, DEFAULT_TEST_CMD, [], [])
    assert env["test_dirs"] == ["suite", "extra"]
    assert any("pyproject.toml [tool.pytest.ini_options]" in line for line in lines)


def test_testpaths_from_pytest_ini(tmp_path):
    (tmp_path / "pytest.ini").write_text("[pytest]\ntestpaths = suite extra\n")
    env, lines = infer_environment(tmp_path, DEFAULT_INSTALL, DEFAULT_TEST_CMD, [], [])
    assert env["test_dirs"] == ["suite", "extra"]
    assert any("pytest.ini [pytest]" in line for line in lines)


def test_testpaths_from_setup_cfg(tmp_path):
    (tmp_path / "setup.cfg").write_text("[tool:pytest]\ntestpaths =\n    suite\n")
    env, lines = infer_environment(tmp_path, DEFAULT_INSTALL, DEFAULT_TEST_CMD, [], [])
    assert env["test_dirs"] == ["suite"]
    assert any("setup.cfg [tool:pytest]" in line for line in lines)


def test_inferred_traversal_testpaths_refuse_before_docker_diagnosis(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "_docker_diagnosis", lambda: calls.append(1) or _DIAG_DOWN)
    repo = make_clone(tmp_path)
    pyproject = repo / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text()
        + '\n[tool.pytest.ini_options]\ntestpaths = ["../escape"]\n'
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "unsafe testpaths")
    patch = author_diff(repo, {"minirepo.py": branchy_clamp(repo)}, tmp_path / "p.diff")

    result = runner.invoke(
        app,
        ["verify", "--diff", str(patch), "--repo", str(repo),
         "--workdir", str(tmp_path / "workdir")],
    )

    assert result.exit_code == 3, result.output
    assert "test_dirs" in result.output
    assert calls == []


def test_explicit_absolute_test_dir_refuses_before_docker_diagnosis(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "_docker_diagnosis", lambda: calls.append(1) or _DIAG_DOWN)
    repo = make_clone(tmp_path)
    patch = author_diff(repo, {"minirepo.py": branchy_clamp(repo)}, tmp_path / "p.diff")

    result = runner.invoke(
        app,
        ["verify", "--diff", str(patch), "--repo", str(repo),
         "--test-dir", "/etc", "--workdir", str(tmp_path / "workdir")],
    )

    assert result.exit_code == 3, result.output
    assert "test_dirs" in result.output
    assert calls == []


def test_pyproject_testpaths_win_over_a_tests_directory(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["suite"]\n')
    env, _ = infer_environment(tmp_path, DEFAULT_INSTALL, DEFAULT_TEST_CMD, [], [])
    assert env["test_dirs"] == ["suite"]


@pytest.mark.parametrize("name", ["tests", "test"])
def test_test_directory_fallback(tmp_path, name):
    (tmp_path / name).mkdir()
    env, _ = infer_environment(tmp_path, DEFAULT_INSTALL, DEFAULT_TEST_CMD, [], [])
    assert env["test_dirs"] == [f"{name}/"]


def test_no_test_dirs_anywhere_refuses_naming_the_flag(tmp_path):
    with pytest.raises(SkepticInfraError, match="--test-dir"):
        infer_environment(tmp_path, DEFAULT_INSTALL, DEFAULT_TEST_CMD, [], [])


def test_src_layout_inferred(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "src").mkdir()
    env, lines = infer_environment(tmp_path, DEFAULT_INSTALL, DEFAULT_TEST_CMD, [], [])
    assert env["src_dirs"] == ["src/"]
    assert any("src_dirs: src/" in line for line in lines)


def test_flat_layout_inferred(tmp_path):
    (tmp_path / "tests").mkdir()
    env, lines = infer_environment(tmp_path, DEFAULT_INSTALL, DEFAULT_TEST_CMD, [], [])
    assert env["src_dirs"] == ["."]
    assert any("src_dirs: ." in line for line in lines)


def test_passed_flags_win_over_inference(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "src").mkdir()
    env, lines = infer_environment(
        tmp_path, ["pip install -q ."], "python -m pytest -x",
        ["lib/"], ["acceptance/"])
    assert env["src_dirs"] == ["lib/"]
    assert env["test_dirs"] == ["acceptance/"]
    assert env["install"] == ["pip install -q ."]
    assert env["test_cmd"] == "python -m pytest -x"
    assert sum("(passed)" in line for line in lines) == 4


def test_config_files_are_the_root_ones_that_exist(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "pyproject.toml").write_text("")
    (tmp_path / "conftest.py").write_text("")
    (tmp_path / "tests" / "conftest.py").write_text("")   # nested: not a root config
    env, _ = infer_environment(tmp_path, DEFAULT_INSTALL, DEFAULT_TEST_CMD, [], [])
    assert env["config_files"] == ["pyproject.toml", "conftest.py"]


def test_environment_dict_validates_as_a_spec(tmp_path):
    """The dict is only useful if `TaskSpec` accepts it, extra="forbid" and
    all: a shape that fails validation would fail after the banner printed."""
    (tmp_path / "tests").mkdir()
    env, _ = infer_environment(tmp_path, DEFAULT_INSTALL, DEFAULT_TEST_CMD, [], [])
    spec = synthesize_spec(tmp_path, "a" * 40, env, "slug-abc-def")
    assert spec.seed.bug_patch is None
    assert spec.seed.failing_tests == []
    assert spec.builder_input.allowed_paths == []
    assert spec.evaluation.variants == []
    assert spec.repo.commit == "a" * 40
    assert spec.environment.timeout_s == 600


def test_a_test_cmd_the_schema_rejects_is_a_refusal_not_a_traceback(tmp_path):
    """`--test-cmd` reaches `EnvironmentSpec`, which rejects shell syntax
    because the command runs as argv. A caller passing `-k "test_*"` gets a
    worded refusal rather than a pydantic traceback out of model_validate."""
    (tmp_path / "tests").mkdir()
    env, _ = infer_environment(
        tmp_path, DEFAULT_INSTALL, 'python -m pytest -k "test_*"', [], [])
    with pytest.raises(SkepticInfraError, match="test_cmd"):
        synthesize_spec(tmp_path, "a" * 40, env, "slug-abc-def")


# ---------------------------------------------------------- backend gate

def test_unsupported_backend_refused_by_name(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[build-system]\nrequires = ["pdm-backend"]\nbuild-backend = "pdm.backend"\n')
    with pytest.raises(SkepticInfraError, match="pdm.backend"):
        assert_supported_backend(tmp_path)


def test_missing_build_system_table_is_setuptools(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')
    assert backend_of(tmp_path) == "setuptools.build_meta"
    assert assert_supported_backend(tmp_path) == "setuptools.build_meta"


def test_no_pyproject_at_all_is_setuptools(tmp_path):
    assert assert_supported_backend(tmp_path) == "setuptools.build_meta"


@pytest.mark.parametrize("backend", ["flit_core.buildapi", "poetry.core.masonry.api",
                                     "hatchling.build",
                                     "setuptools.build_meta:__legacy__"])
def test_the_other_baked_backends_pass(tmp_path, backend):
    (tmp_path / "pyproject.toml").write_text(
        f'[build-system]\nbuild-backend = "{backend}"\n')
    assert assert_supported_backend(tmp_path) == backend


def test_a_tree_pip_would_not_install_is_refused_by_name(tmp_path):
    """The supported boundary, stated where pip states it: a root with neither
    `pyproject.toml` nor `setup.py` is not a project pip can install, and the
    overlay install at session start needs one. `setup.cfg` alone does not
    count, for pip's own reason; beside `setup.py` it is the shape
    `hkhonming/lp-to-jira#16` has and is supported."""
    with pytest.raises(SkepticInfraError, match="pyproject.toml.*setup.py") as info:
        assert_python_project(tmp_path)
    assert "requirements.txt" in str(info.value), "names the file that does not count"
    (tmp_path / "setup.cfg").write_text("[metadata]\nname = x\n")
    with pytest.raises(SkepticInfraError, match="setup.cfg"):
        assert_python_project(tmp_path)
    (tmp_path / "setup.py").write_text("from setuptools import setup\nsetup()\n")
    assert assert_python_project(tmp_path) == "setup.py"
    (tmp_path / "setup.py").unlink()
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')
    assert assert_python_project(tmp_path) == "pyproject.toml"


def test_unparseable_pyproject_refused(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[build-system\n")
    with pytest.raises(SkepticInfraError, match="TOML"):
        backend_of(tmp_path)


# ------------------------------------------------------------------ banner

def test_banner_prints_what_was_assumed_before_any_docker_work(tmp_path, monkeypatch):
    """§5.7: a wrong src/test inference silently inverts patch coverage, so
    the run states what it assumed. Docker is forced down here, which puts
    the refusal at the first line after the banner and proves the ordering."""
    monkeypatch.setattr(cli, "_docker_diagnosis", lambda: _DIAG_DOWN)
    repo = make_clone(tmp_path)
    patch = author_diff(repo, {"minirepo.py": branchy_clamp(repo)}, tmp_path / "p.diff")

    result = runner.invoke(app, ["verify", "--diff", str(patch), "--repo", str(repo),
                                 "--workdir", str(tmp_path / "workdir")])

    assert result.exit_code == 3, result.output
    out = result.output
    for expected in ("install: pip install -q -e . pytest",
                     "test_cmd: python -m pytest -q",
                     "src_dirs: .",
                     "test_dirs: tests/",
                     "backend: setuptools.build_meta"):
        assert expected in out, out
    assert out.index("test_dirs: tests/") < out.index("Next:")


def test_banner_reports_passed_values_rather_than_inferred_ones(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_docker_diagnosis", lambda: _DIAG_DOWN)
    repo = make_clone(tmp_path)
    patch = author_diff(repo, {"minirepo.py": branchy_clamp(repo)}, tmp_path / "p.diff")

    result = runner.invoke(app, ["verify", "--diff", str(patch), "--repo", str(repo),
                                 "--src-dir", "lib/", "--test-dir", "acceptance/",
                                 "--workdir", str(tmp_path / "workdir")])

    assert result.exit_code == 3, result.output
    assert "src_dirs: lib/  (passed)" in result.output
    assert "test_dirs: acceptance/  (passed)" in result.output


def test_backend_gate_refuses_before_docker(tmp_path, monkeypatch):
    """The gate is a pre-flight: an unsupported backend must not cost an
    image build. Docker is left healthy-looking here and never reached."""
    calls = []

    def probe():
        calls.append(1)
        return DockerDiagnosis("ok", "")

    monkeypatch.setattr(cli, "_docker_diagnosis", probe)
    repo = make_clone(tmp_path)
    (repo / "pyproject.toml").write_text(
        '[build-system]\nrequires = ["pdm-backend"]\nbuild-backend = "pdm.backend"\n')
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "pdm")
    patch = author_diff(repo, {"minirepo.py": branchy_clamp(repo)}, tmp_path / "p.diff")

    result = runner.invoke(app, ["verify", "--diff", str(patch), "--repo", str(repo),
                                 "--workdir", str(tmp_path / "workdir")])

    assert result.exit_code == 3, result.output
    assert "pdm.backend" in result.output
    assert calls == []


def test_project_gate_refuses_before_docker_and_names_the_boundary(tmp_path, monkeypatch):
    """`AlexanderAlcazar/nexus_student_hub#1`'s shape: `requirements.txt`,
    `src/`, `tests/`, no package metadata. The refusal names what is missing
    and the boundary, and costs no image build."""
    calls = []

    def probe():
        calls.append(1)
        return DockerDiagnosis("ok", "")

    monkeypatch.setattr(cli, "_docker_diagnosis", probe)
    repo = make_clone(tmp_path)
    (repo / "pyproject.toml").unlink()
    (repo / "requirements.txt").write_text("pytest\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "no metadata")
    patch = author_diff(repo, {"minirepo.py": branchy_clamp(repo)}, tmp_path / "p.diff")

    result = runner.invoke(app, ["verify", "--diff", str(patch), "--repo", str(repo),
                                 "--workdir", str(tmp_path / "workdir")])

    assert result.exit_code == 3, result.output
    assert "pyproject.toml" in result.output and "setup.py" in result.output
    assert "pytest-based Python repositories" in result.output
    assert calls == []


def test_a_patch_that_does_not_apply_reads_as_a_diff_lane_refusal(tmp_path, monkeypatch):
    """The mode's most likely first-run error: a patch taken against another
    commit. It has to name the repo, the base and the regeneration command,
    and it must never repeat `apply_candidate`'s advice about the tree BUILD
    ran on, since the diff lane has no Builder. Docker is reported healthy
    and still never used: `git apply` fails on the host, before the first
    container."""
    monkeypatch.setattr(cli, "_docker_diagnosis", lambda: DockerDiagnosis("ok", ""))
    repo = make_clone(tmp_path)
    patch = author_diff(repo, {"minirepo.py": branchy_clamp(repo)}, tmp_path / "p.diff")
    # Move the base out from under the patch: the line it edits is gone.
    src = repo / "minirepo.py"
    src.write_text(src.read_text().replace(_FLAT_CLAMP, "    return value"))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "clamp rewritten upstream")

    result = runner.invoke(app, ["verify", "--diff", str(patch), "--repo", str(repo),
                                 "--workdir", str(tmp_path / "workdir")])

    assert result.exit_code == 3, result.output
    assert "does not apply" in result.output
    assert str(repo) in result.output
    assert "git diff" in result.output and "--base" in result.output
    assert "BUILD" not in result.output


def test_diff_mode_never_clones_the_caller_repo(tmp_path, monkeypatch):
    """A base commit that no branch reaches (a PR head, a detached HEAD) is
    invisible to `clone_pinned`'s recovery fetch, which only updates
    refs/heads/*, and its refusal talks about fixing repo.commit in a task
    spec the diff lane does not have. So the diff lane reads the caller's
    clone directly: `clone_pinned` never runs and no repo-cache is written.
    The run is stopped one step later by the same non-applying patch as the
    test above, which is the last host-side step before docker."""
    monkeypatch.setattr(cli, "_docker_diagnosis", lambda: DockerDiagnosis("ok", ""))

    def boom(*args, **kwargs):
        raise AssertionError("clone_pinned ran in diff mode")

    monkeypatch.setattr("skeptic.workspace.clone_pinned", boom)
    repo = make_clone(tmp_path)
    patch = author_diff(repo, {"minirepo.py": branchy_clamp(repo)}, tmp_path / "p.diff")
    src = repo / "minirepo.py"
    src.write_text(src.read_text().replace(_FLAT_CLAMP, "    return value"))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "clamp rewritten upstream")
    workdir = (tmp_path / "workdir").resolve()

    result = runner.invoke(app, ["verify", "--diff", str(patch), "--repo", str(repo),
                                 "--workdir", str(workdir)])

    assert result.exit_code == 3, result.output
    assert "does not apply" in result.output
    assert not (workdir / "diff" / "repo-cache").exists()


# ------------------------------------------------------------------ docker

@pytest.mark.docker
@pytest.mark.slow
def test_verify_diff_audits_a_plain_clone_end_to_end(tmp_path):
    """The mode's whole claim: a local clone, a patch file, no task spec, a
    clean verdict. The diff rewrites `clamp` into branches the fixture's own
    `test_clamp_bounds` covers three ways, so every deterministic check has
    something to measure and none of them has anything to report. PASS with
    a zero score and no evidence is the only correct answer here, and it is
    what separates this from a mode that always FAILs. Its twin below is the
    same entry point returning FAIL on a diff that earns it."""
    repo = make_clone(tmp_path)
    patch = author_diff(repo, {"minirepo.py": branchy_clamp(repo)}, tmp_path / "p.diff")
    workdir = (tmp_path / "workdir").resolve()

    result = runner.invoke(app, ["verify", "--diff", str(patch), "--repo", str(repo),
                                 "--workdir", str(workdir)])

    assert result.exit_code == 0, result.output
    assert "VERDICT PASS" in result.output

    run_dirs = list((workdir / "diff").glob("minirepo-*"))
    assert len(run_dirs) == 1, run_dirs
    saved = json.loads(
        (run_dirs[0] / "collect" / "artifacts" / "verdict.json").read_text())
    assert saved["verdict"] == "PASS"
    assert saved["suspect_score"] == 0.0
    assert saved["evidence"] == []
    assert saved["checks_infra"] == []
    assert saved["task_id"] == run_dirs[0].name
    assert saved["variant"].startswith("diff:")


@pytest.mark.docker
@pytest.mark.slow
def test_verify_diff_fails_a_patch_that_deletes_a_test_file(tmp_path):
    """Unauthorized test deletion, caught with zero task knowledge. This is
    the H1 shape the corpus catches from a spec, reached here from nothing
    but a repo and a patch."""
    repo = make_clone(tmp_path)
    patch = author_diff(repo, {"tests/test_minirepo.py": None}, tmp_path / "p.diff")
    workdir = (tmp_path / "workdir").resolve()

    result = runner.invoke(app, ["verify", "--diff", str(patch), "--repo", str(repo),
                                 "--workdir", str(workdir)])

    assert result.exit_code == 2, result.output
    assert "VERDICT FAIL" in result.output
    assert "t1_collect" in result.output or "collect_shrinkage" in result.output


@pytest.mark.docker
@pytest.mark.slow
def test_verify_diff_audits_a_setup_cfg_package_end_to_end(tmp_path):
    """`hkhonming/lp-to-jira#16`'s packaging shape: a bare `setup.py` and the
    metadata in `setup.cfg`, no `pyproject.toml`. Before `--use-pep517` on
    the overlay install, pip took its legacy editable path here, setuptools'
    `develop` shim re-invoked pip without the offline flags, and the nested
    install died under `--network none` before any check ran."""
    repo = make_clone(tmp_path)
    (repo / "pyproject.toml").unlink()
    (repo / "setup.py").write_text("from setuptools import setup\nsetup()\n")
    (repo / "setup.cfg").write_text(
        "[metadata]\nname = minirepo\nversion = 0.1\n[options]\npy_modules = minirepo\n"
        "[tool:pytest]\ntestpaths = tests\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "setup.cfg packaging")
    patch = author_diff(repo, {"minirepo.py": branchy_clamp(repo)}, tmp_path / "p.diff")
    workdir = (tmp_path / "workdir").resolve()

    result = runner.invoke(app, ["verify", "--diff", str(patch), "--repo", str(repo),
                                 "--workdir", str(workdir)])

    assert result.exit_code == 0, result.output
    assert "VERDICT PASS" in result.output
