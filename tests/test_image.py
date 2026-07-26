import pytest

from skeptic.image import BASE_IMAGE, render_dockerfile, repo_image_tag
from tests.helpers import make_task_spec  # reuse the existing spec-builder helper


def test_base_image_is_digest_pinned():
    assert "@sha256:" in BASE_IMAGE


def test_repo_image_tag_keys_on_repo_commit_and_environment():
    spec = make_task_spec()
    tag = repo_image_tag(spec)
    slug = spec.repo.url.rstrip("/").rsplit("/", 1)[-1]
    assert tag.startswith(f"skeptic-repo-{slug}:{spec.repo.commit[:12]}-")
    changed = spec.model_copy(deep=True)
    changed.environment.install = ["pip install -q -e . pytest extradep"]
    assert repo_image_tag(changed) != tag


def test_repo_image_tag_moves_when_base_image_changes(monkeypatch):
    # A digest bump to BASE_IMAGE must not leave the tag (and therefore the
    # cached image) unchanged: repo_image_tag hashes the rendered Dockerfile,
    # which interpolates BASE_IMAGE, not just environment.install.
    spec = make_task_spec()
    tag = repo_image_tag(spec)
    monkeypatch.setattr("skeptic.image.BASE_IMAGE", "python@sha256:" + "0" * 64)
    assert repo_image_tag(spec) != tag


def test_render_dockerfile_two_stages_no_source_in_final(tmp_path):
    spec = make_task_spec()
    text = render_dockerfile(spec)
    assert text.count(f"FROM {BASE_IMAGE}") == 2
    resolve, final = text.split("FROM " + BASE_IMAGE)[1:]
    assert "COPY . /src" in resolve
    for cmd in spec.environment.install:
        assert cmd in resolve
    assert "pip freeze --exclude-editable" in resolve
    assert "COPY ." not in final          # no source, only the constraints file
    assert "constraints.txt" in final
    # editable installs at session start need the backends offline
    for backend in ("flit_core", "poetry-core", "setuptools", "hatchling", "wheel"):
        assert backend in resolve


@pytest.mark.docker
@pytest.mark.slow
def test_ensure_repo_image_builds_and_freezes(tmp_path, minirepo_spec_and_repo):
    from skeptic.image import ensure_repo_image
    from skeptic.workspace import materialize

    spec, repo_dir = minirepo_spec_and_repo   # session fixture from Step 1
    pristine = tmp_path / "pristine"
    materialize(repo_dir, spec.repo.commit, pristine)
    ref = ensure_repo_image(spec, pristine, tmp_path / "img")
    assert ref.image_id.startswith("sha256:")
    assert "pytest==" in ref.constraints_path.read_text()
    # second call reuses the image without a rebuild
    again = ensure_repo_image(spec, pristine, tmp_path / "img")
    assert again.image_id == ref.image_id
