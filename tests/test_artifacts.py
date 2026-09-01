import os

import pytest

from skeptic.artifacts import (
    ArtifactSpec,
    admit_artifacts,
    publish_artifact_bytes,
    read_artifact_bytes,
    read_artifact_text,
    validate_artifact_path,
)
from skeptic.errors import SkepticInfraError


def test_admission_rejects_final_symlink(tmp_path):
    quarantine, sealed = tmp_path / "quarantine", tmp_path / "sealed"
    quarantine.mkdir()
    (tmp_path / "outside").write_bytes(b"harmless")
    (quarantine / "result").symlink_to(tmp_path / "outside")
    with pytest.raises(SkepticInfraError, match="symbolic link"):
        admit_artifacts(quarantine, sealed, [ArtifactSpec("result", 64)])


def test_admission_rejects_symlinked_parent(tmp_path):
    quarantine, sealed = tmp_path / "quarantine", tmp_path / "sealed"
    quarantine.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "result").write_bytes(b"outside-data")
    (quarantine / "nested").symlink_to(outside, target_is_directory=True)
    with pytest.raises(SkepticInfraError, match="symbolic link"):
        admit_artifacts(quarantine, sealed, [ArtifactSpec("nested/result", 64)])


def test_admission_rejects_optional_symlink(tmp_path):
    quarantine, sealed = tmp_path / "quarantine", tmp_path / "sealed"
    quarantine.mkdir()
    (tmp_path / "outside").write_bytes(b"harmless")
    (quarantine / "result").symlink_to(tmp_path / "outside")
    with pytest.raises(SkepticInfraError, match="symbolic link"):
        admit_artifacts(
            quarantine,
            sealed,
            [ArtifactSpec("result", 64, required=False)],
        )


def test_admission_rejects_fifo_without_opening_it(tmp_path):
    quarantine, sealed = tmp_path / "quarantine", tmp_path / "sealed"
    quarantine.mkdir()
    os.mkfifo(quarantine / "result")
    with pytest.raises(SkepticInfraError, match="regular file"):
        admit_artifacts(quarantine, sealed, [ArtifactSpec("result", 64)])


def test_admission_rejects_directory(tmp_path):
    quarantine, sealed = tmp_path / "quarantine", tmp_path / "sealed"
    quarantine.mkdir()
    (quarantine / "result").mkdir()
    with pytest.raises(SkepticInfraError, match="regular file"):
        admit_artifacts(quarantine, sealed, [ArtifactSpec("result", 64)])


def test_admission_rejects_cap_plus_one_regular_file(tmp_path):
    quarantine, sealed = tmp_path / "quarantine", tmp_path / "sealed"
    quarantine.mkdir()
    (quarantine / "result").write_bytes(b"12345")
    with pytest.raises(SkepticInfraError, match="4-byte cap"):
        admit_artifacts(quarantine, sealed, [ArtifactSpec("result", 4)])


def test_admission_refuses_existing_final_without_changing_it(tmp_path):
    quarantine, sealed = tmp_path / "quarantine", tmp_path / "sealed"
    quarantine.mkdir()
    sealed.mkdir()
    (quarantine / "result").write_bytes(b"candidate-data")
    (sealed / "result").write_bytes(b"owner-data")
    with pytest.raises(SkepticInfraError, match="already exists"):
        admit_artifacts(quarantine, sealed, [ArtifactSpec("result", 64)])
    assert (sealed / "result").read_bytes() == b"owner-data"


def test_admission_publishes_ordinary_nested_file(tmp_path):
    quarantine, sealed = tmp_path / "quarantine", tmp_path / "sealed"
    (quarantine / "nested").mkdir(parents=True)
    (quarantine / "nested" / "result").write_bytes(b"candidate-data")
    admit_artifacts(quarantine, sealed, [ArtifactSpec("nested/result", 64)])
    assert (sealed / "nested" / "result").read_bytes() == b"candidate-data"


def test_safe_reader_rejects_parent_traversal(tmp_path):
    root = tmp_path / "sealed"
    root.mkdir()
    (tmp_path / "outside").write_bytes(b"outside-data")
    with pytest.raises(SkepticInfraError, match="parent traversal"):
        read_artifact_bytes(root, "../outside", 64)


def test_safe_readers_return_literal_bytes_text_and_path(tmp_path):
    root = tmp_path / "sealed"
    (root / "nested").mkdir(parents=True)
    artifact = root / "nested" / "result"
    artifact.write_bytes(b"sealed-data")
    assert read_artifact_bytes(root, "nested/result", 64) == b"sealed-data"
    assert read_artifact_text(root, "nested/result", 64) == "sealed-data"
    assert validate_artifact_path(root, "nested/result", 64) == artifact
    assert read_artifact_bytes(root, "missing", 64, required=False) is None


def test_publish_artifact_bytes_uses_no_replace_destination(tmp_path):
    sealed = tmp_path / "sealed"
    publish_artifact_bytes(sealed, "nested/result", b"host-data", 64)
    assert (sealed / "nested" / "result").read_bytes() == b"host-data"
    with pytest.raises(SkepticInfraError, match="already exists"):
        publish_artifact_bytes(sealed, "nested/result", b"replacement", 64)
    assert (sealed / "nested" / "result").read_bytes() == b"host-data"
