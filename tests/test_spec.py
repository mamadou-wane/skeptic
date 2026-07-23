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
