import pytest

from skeptic.sandbox import DockerDiagnosis
from tests import conftest as hooks


class DockerItem:
    def __init__(self):
        self.keywords = {"docker": True}
        self.markers = []

    def add_marker(self, marker):
        self.markers.append(marker)


def test_local_missing_docker_marks_docker_tests_skipped(monkeypatch):
    monkeypatch.delenv("SKEPTIC_REQUIRE_DOCKER", raising=False)
    monkeypatch.setattr(
        hooks,
        "docker_diagnosis",
        lambda: DockerDiagnosis("unreachable", "daemon down"),
        raising=False,
    )
    item = DockerItem()
    hooks.pytest_collection_modifyitems(None, [item])
    assert len(item.markers) == 1


def test_required_missing_docker_raises_usage_error(monkeypatch):
    monkeypatch.setenv("SKEPTIC_REQUIRE_DOCKER", "1")
    monkeypatch.setattr(
        hooks,
        "docker_diagnosis",
        lambda: DockerDiagnosis("unreachable", "daemon down"),
        raising=False,
    )
    with pytest.raises(pytest.UsageError, match="unreachable.*daemon down"):
        hooks.pytest_collection_modifyitems(None, [DockerItem()])


def test_required_available_docker_does_not_mark_or_raise(monkeypatch):
    monkeypatch.setenv("SKEPTIC_REQUIRE_DOCKER", "1")
    monkeypatch.setattr(
        hooks,
        "docker_diagnosis",
        lambda: DockerDiagnosis("ok", ""),
        raising=False,
    )
    item = DockerItem()
    hooks.pytest_collection_modifyitems(None, [item])
    assert item.markers == []
