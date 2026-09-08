import json

from skeptic.cli import _build_cache_key
from skeptic.orchestrator import StageCache
from skeptic.spec import VariantSpec
from tests.helpers import baseline_cache_key as _baseline_key
from tests.helpers import make_task_spec
from tests.helpers import verify_cache_key as _verify_cache_key


def test_same_path_dependency_content_changes_verification_and_baseline_keys(tmp_path):
    pin = tmp_path / "constraints.txt"
    pin.write_text("pytest==8.0.0\n")
    spec = make_task_spec()
    spec = spec.model_copy(update={"environment": spec.environment.model_copy(
        update={"constraints": str(pin)})})
    variant = spec.evaluation.variants[0]
    before = (_verify_cache_key(spec, variant, "deterministic"), _baseline_key(spec, ["x.py"]))
    pin.write_text("pytest==9.0.0\n")
    after = (_verify_cache_key(spec, variant, "deterministic"), _baseline_key(spec, ["x.py"]))
    assert before[0] != after[0]
    assert before[1] != after[1]


def test_paid_key_includes_clean_filter_contents(tmp_path):
    patch = tmp_path / "reference.diff"
    patch.write_text("reference one")
    spec = make_task_spec()
    variant = spec.evaluation.variants[0]
    spec = spec.model_copy(update={"evaluation": spec.evaluation.model_copy(update={
        "variants": [variant, VariantSpec(id="gold-prime", patch=str(patch), label="clean")],
    })})
    before = _verify_cache_key(spec, variant, "paid")
    patch.write_text("reference two")
    assert _verify_cache_key(spec, variant, "paid") != before


def test_build_key_includes_green_predicate_inputs():
    spec = make_task_spec()
    changed = spec.model_copy(update={"seed": spec.seed.model_copy(
        update={"quarantine": ["test_ignored"], "failing_tests": ["test_other"]})})
    assert (_build_cache_key(spec, "model", "image", "seed")
            != _build_cache_key(changed, "model", "image", "seed"))


def test_legacy_cache_entry_does_not_inherit_new_contract(tmp_path):
    cache = StageCache(tmp_path)
    (tmp_path / "old.json").write_text(json.dumps({"verdict": "PASS"}))
    assert cache.get("old") is None


def test_cache_refuses_changed_or_missing_required_artifact(tmp_path):
    artifact = tmp_path / "observed.json"
    artifact.write_text("original")
    cache = StageCache(tmp_path / "cache")
    value = {"verdict": "PASS", "_cache_artifacts": [str(artifact)]}
    cache.put("key", value)
    assert cache.get("key") == value
    artifact.write_text("changed")
    assert cache.get("key") is None
    artifact.unlink()
    assert cache.get("key") is None


def test_reused_image_checks_live_closure_instead_of_host_copy(tmp_path, monkeypatch):
    import subprocess

    import pytest

    from skeptic.errors import SkepticInfraError
    from skeptic.image import ensure_repo_image

    pin = tmp_path / "pin.txt"
    pin.write_text("pytest==8.0.0\n")
    work = tmp_path / "image"
    work.mkdir()
    (work / "constraints.txt").write_text(pin.read_text())
    spec = make_task_spec()
    spec = spec.model_copy(update={"environment": spec.environment.model_copy(
        update={"constraints": str(pin)})})

    def docker(args, **kwargs):
        text = "sha256:actual\n" if args[:2] == ["image", "inspect"] else "pytest==9.0.0\n"
        return subprocess.CompletedProcess(args, 0, text, "")

    monkeypatch.setattr("skeptic.image._docker", docker)
    with pytest.raises(SkepticInfraError, match="differs from the committed pin"):
        ensure_repo_image(spec, tmp_path / "pristine", work)


def test_execution_image_changes_every_execution_cache_key():
    spec = make_task_spec()
    variant = spec.evaluation.variants[0]
    assert (_verify_cache_key(spec, variant, "deterministic", image_id="sha256:one")
            != _verify_cache_key(spec, variant, "deterministic", image_id="sha256:two"))
    assert (_baseline_key(spec, ["x.py"], image_id="sha256:one")
            != _baseline_key(spec, ["x.py"], image_id="sha256:two"))
    assert (_build_cache_key(spec, "model", "sha256:one", "seed")
            != _build_cache_key(spec, "model", "sha256:two", "seed"))


def test_frozen_inputs_survive_original_file_changes(tmp_path):
    from skeptic.run_inputs import file_hash, freeze_inputs
    seed, clean, pin = [tmp_path / name for name in ("seed.diff", "clean.diff", "pin.txt")]
    for p in (seed, clean, pin):
        p.write_bytes(b"original\r\n")
    spec = make_task_spec()
    spec = spec.model_copy(update={
        "seed": spec.seed.model_copy(update={"bug_patch": str(seed)}),
        "environment": spec.environment.model_copy(update={"constraints": str(pin)}),
        "evaluation": spec.evaluation.model_copy(update={"variants": [
            VariantSpec(id="gold", patch=str(clean), label="clean")]}),
    })
    frozen, _ = freeze_inputs(spec, tmp_path / "run")
    before = _verify_cache_key(frozen, frozen.evaluation.variants[0], "paid")
    for p in (seed, clean, pin):
        p.write_bytes(b"changed")
    assert _verify_cache_key(frozen, frozen.evaluation.variants[0], "paid") == before
    assert file_hash(frozen.seed.bug_patch) != file_hash(seed)


def test_input_and_cache_write_failures_are_infrastructure_errors(tmp_path, monkeypatch):
    from pathlib import Path

    import pytest

    from skeptic.errors import SkepticInfraError
    from skeptic.run_inputs import capture_bytes

    def denied(*args, **kwargs):
        raise PermissionError("read-only output")

    with monkeypatch.context() as patcher:
        patcher.setattr(Path, "open", denied)
        with pytest.raises(SkepticInfraError):
            capture_bytes(b"input", tmp_path, "input.diff")


def test_cache_publication_failure_is_infrastructure_error(tmp_path, monkeypatch):
    from pathlib import Path

    import pytest

    from skeptic.errors import SkepticInfraError

    cache = StageCache(tmp_path / "cache")
    def denied(*args, **kwargs):
        raise PermissionError("read-only output")
    monkeypatch.setattr(Path, "write_text", denied)
    with pytest.raises(SkepticInfraError):
        cache.put("key", {"value": 1})
