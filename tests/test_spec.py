# tests/test_spec.py
from pathlib import Path

import pytest

from skeptic.errors import SkepticInfraError
from skeptic.spec import TaskSpec, find_task, load_task

FIXTURES = Path(__file__).parent / "fixtures" / "specs"


def test_valid_task_loads():
    spec = load_task(FIXTURES / "valid-task.yaml")
    assert isinstance(spec, TaskSpec)
    assert spec.task_id == "click-0001"
    assert spec.repo.commit.startswith("aaaabbbb")
    assert spec.environment.timeout_s == 600
    assert spec.seed.failing_tests == ["tests/test_termui.py::test_progressbar_width"]
    assert spec.evaluation.variants[0].label == "clean"


def test_unknown_field_rejected(tmp_path):
    text = (FIXTURES / "valid-task.yaml").read_text().replace(
        "task_id: click-0001", "task_id: click-0001\nsurprise_field: 1"
    )
    p = tmp_path / "bad.yaml"
    p.write_text(text)
    with pytest.raises(SkepticInfraError, match="surprise_field"):
        load_task(p)


def test_missing_required_field_names_it(tmp_path):
    text = (FIXTURES / "valid-task.yaml").read_text().replace("  bug_patch: patches/click-0001-seed.diff\n", "")
    p = tmp_path / "bad.yaml"
    p.write_text(text)
    with pytest.raises(SkepticInfraError, match="bug_patch"):
        load_task(p)


def test_no_clean_variant_rejected(tmp_path):
    text = (FIXTURES / "valid-task.yaml").read_text().replace("label: clean", "label: hacked")
    p = tmp_path / "bad.yaml"
    p.write_text(text)
    with pytest.raises(SkepticInfraError, match="clean"):
        load_task(p)


def test_missing_file_is_infra_error_with_next_command(tmp_path):
    with pytest.raises(SkepticInfraError, match="skeptic tasks list"):
        load_task(tmp_path / "nope.yaml")


def test_find_task_by_id(tmp_path):
    dest = tmp_path / "click-0001.yaml"
    dest.write_text((FIXTURES / "valid-task.yaml").read_text())
    spec = find_task("click-0001", tmp_path)
    assert spec.task_id == "click-0001"
    with pytest.raises(SkepticInfraError, match="click-9999"):
        find_task("click-9999", tmp_path)


def test_environment_config_files_defaults_empty():
    spec = load_task(FIXTURES / "valid-task.yaml")
    assert spec.environment.config_files == []


# 2026-07-26 review finding 6: seed --check runs test_cmd through sh -c,
# BUILD runs it through shlex.split + exec_argv (no shell). Reject anything
# where those two disagree at spec load, before BUILD spends money finding
# the divergence with an image already built.


def test_test_cmd_with_shell_metacharacter_rejected(tmp_path):
    text = (FIXTURES / "valid-task.yaml").read_text().replace(
        'test_cmd: "python -m pytest -q"',
        'test_cmd: "python -m pytest -q && rm -rf /"',
    )
    p = tmp_path / "bad.yaml"
    p.write_text(text)
    with pytest.raises(SkepticInfraError, match="shell metacharacter"):
        load_task(p)


def test_test_cmd_with_env_prefix_rejected(tmp_path):
    text = (FIXTURES / "valid-task.yaml").read_text().replace(
        'test_cmd: "python -m pytest -q"',
        'test_cmd: "COLUMNS=80 python -m pytest -q"',
    )
    p = tmp_path / "bad.yaml"
    p.write_text(text)
    with pytest.raises(SkepticInfraError, match="environment-variable"):
        load_task(p)


def test_plain_test_cmd_still_loads():
    spec = load_task(FIXTURES / "valid-task.yaml")
    assert spec.environment.test_cmd == "python -m pytest -q"


# 2026-07-26 review finding 4: shlex.split and sh -c tokenize a quoted
# argument identically. Banning `"` and `'` would remove the only way to
# express an argument containing a space, since test_cmd is one string.


def test_quoted_test_cmd_loads_and_round_trips_to_expected_argv(tmp_path):
    import shlex

    text = (FIXTURES / "valid-task.yaml").read_text().replace(
        'test_cmd: "python -m pytest -q"',
        "test_cmd: 'python -m pytest -q -k \"not slow\"'",
    )
    p = tmp_path / "quoted.yaml"
    p.write_text(text)
    spec = load_task(p)
    assert spec.environment.test_cmd == 'python -m pytest -q -k "not slow"'
    assert shlex.split(spec.environment.test_cmd) == [
        "python", "-m", "pytest", "-q", "-k", "not slow",
    ]


def test_unbalanced_quote_in_test_cmd_rejected_at_load(tmp_path):
    text = (FIXTURES / "valid-task.yaml").read_text().replace(
        'test_cmd: "python -m pytest -q"',
        "test_cmd: 'python -m pytest -q -k \"not slow'",
    )
    p = tmp_path / "bad.yaml"
    p.write_text(text)
    with pytest.raises(SkepticInfraError, match="unbalanced quoting"):
        load_task(p)
