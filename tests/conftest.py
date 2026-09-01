import os

import pytest

from skeptic.sandbox import docker_diagnosis


def pytest_collection_modifyitems(config, items):
    diagnosis = docker_diagnosis()
    if diagnosis.state == "ok":
        return
    if os.environ.get("SKEPTIC_REQUIRE_DOCKER") == "1":
        raise pytest.UsageError(
            "Docker is required for this test run but unavailable "
            f"({diagnosis.state}: {diagnosis.detail})"
        )
    skip = pytest.mark.skip(reason="Docker daemon not available")
    for item in items:
        if "docker" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def minirepo_spec_and_repo(tmp_path_factory):
    from skeptic.spec import find_task
    from tests.helpers import make_minirepo_task

    root = tmp_path_factory.mktemp("minirepo-task")
    tasks_dir, task_id = make_minirepo_task(root)
    spec = find_task(task_id, tasks_dir)
    return spec, root / "minirepo-upstream"
