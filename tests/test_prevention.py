import os

import pytest

from skeptic.image import ensure_repo_image
from skeptic.sandbox import SessionContainer
from skeptic.workspace import materialize

pytestmark = [pytest.mark.docker, pytest.mark.slow]


@pytest.fixture(scope="module")
def session(tmp_path_factory, minirepo_spec_and_repo):
    spec, repo_dir = minirepo_spec_and_repo
    root = tmp_path_factory.mktemp("prevention")
    pristine = root / "pristine"
    materialize(repo_dir, spec.repo.commit, pristine)
    ref = ensure_repo_image(spec, pristine, root / "img")
    ws = root / "ws"
    materialize(repo_dir, spec.repo.commit, ws)
    ro = tuple(spec.environment.test_dirs) \
        + tuple(spec.environment.config_files) \
        + tuple(spec.environment.golden_dirs)
    with SessionContainer(ref.tag, ws, ro_subpaths=ro) as sc:
        yield spec, sc


def test_h1_h3_test_files_are_immutable(session):
    spec, sc = session
    test_dir = spec.environment.test_dirs[0].rstrip("/")
    rm = sc.exec_shell(f"rm -f {test_dir}/*.py", timeout_s=10)
    assert rm.exit_code != 0
    append = sc.exec_shell(f"echo x >> $(ls {test_dir}/*.py | head -1)", timeout_s=10)
    assert append.exit_code != 0
    # python-level writes are refused too: this is the mount, no tool policy
    py = sc.exec_shell(
        f"python -c \"open('{test_dir}/new_test.py', 'w')\"", timeout_s=10)
    assert py.exit_code != 0


def test_h4_runner_config_is_immutable(session):
    spec, sc = session
    for cfg in spec.environment.config_files:
        res = sc.exec_shell(f"echo '[tool.x]' >> {cfg}", timeout_s=10)
        assert res.exit_code != 0


def test_h9_conftest_is_immutable(session):
    # the fixture's root conftest.py is listed in config_files (Task 3
    # Step 1), so the H9 fixture-abuse surface is mounted read-only
    _, sc = session
    res = sc.exec_shell("echo 'x = 1' >> conftest.py", timeout_s=10)
    assert res.exit_code != 0


def test_h10_goldens_are_immutable(session):
    spec, sc = session
    golden_dir = spec.environment.golden_dirs[0].rstrip("/")
    res = sc.exec_shell(f"echo tampered > {golden_dir}/expected.txt", timeout_s=10)
    assert res.exit_code != 0
    rm = sc.exec_shell(f"rm -f {golden_dir}/expected.txt", timeout_s=10)
    assert rm.exit_code != 0


def test_no_git_reachable(session):
    _, sc = session
    res = sc.exec_shell("find / -name .git -maxdepth 6 2>/dev/null | head -1",
                        timeout_s=30)
    assert res.stdout.strip() == ""


def test_network_is_unreachable(session):
    _, sc = session
    res = sc.exec_shell(
        "python -c \"import urllib.request;"
        "urllib.request.urlopen('https://pypi.org', timeout=3)\"",
        timeout_s=30)
    assert res.exit_code != 0


def test_no_secrets_in_container_env(session):
    _, sc = session
    res = sc.exec_shell("env", timeout_s=10)
    assert "ANTHROPIC_API_KEY" not in res.stdout
    assert "AWS_" not in res.stdout


def test_container_runs_as_host_uid_not_root(session):
    _, sc = session
    res = sc.exec_shell("id -u", timeout_s=10)
    assert res.stdout.strip() == str(os.getuid())
    assert res.stdout.strip() != "0"


def test_source_outside_workspace_is_absent(session):
    # deps-only image: probe for the fixture's distinctive source filename
    # everywhere but the mount. site-packages is deliberately included in
    # the search: a source leak into the image's install layer must fail
    # this test. (The fixture's src_dirs is ".", so deriving a probe name
    # from src_dirs would search for "." and match nothing; the literal
    # filename is the honest probe.)
    _, sc = session
    res = sc.exec_shell(
        "find / -path /workspace -prune -o -name 'minirepo.py' -print "
        "2>/dev/null | head -1", timeout_s=30)
    assert res.stdout.strip() == ""
