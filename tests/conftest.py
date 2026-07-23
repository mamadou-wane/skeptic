import pytest

from skeptic.sandbox import docker_available


def pytest_collection_modifyitems(config, items):
    if docker_available():
        return
    skip = pytest.mark.skip(reason="Docker daemon not available")
    for item in items:
        if "docker" in item.keywords:
            item.add_marker(skip)
