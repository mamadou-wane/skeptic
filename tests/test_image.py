import subprocess

import pytest

from skeptic.image import (
    BASE_IMAGE,
    live_image_digest,
    render_dockerfile,
    repo_image_tag,
)
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


def test_render_dockerfile_installs_harness_coverage_in_resolve():
    spec = make_task_spec()
    text = render_dockerfile(spec)
    resolve, final = text.split("FROM " + BASE_IMAGE)[1:]
    assert "coverage" in resolve
    assert "COPY ." not in final


def test_repo_image_tag_changes_when_harness_tools_change(monkeypatch):
    """Passes by construction: repo_image_tag hashes the whole rendered
    Dockerfile, and _HARNESS_TOOLS is interpolated into that render, so a
    change to it moves the tag with no separate tag-hash input to update.
    This is a regression guard against a future refactor that narrows the
    hash to something short of the whole render.
    """
    spec = make_task_spec()
    tag = repo_image_tag(spec)
    monkeypatch.setattr("skeptic.image._HARNESS_TOOLS", "coverage extra-tool")
    assert repo_image_tag(spec) != tag


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


@pytest.mark.docker
@pytest.mark.slow
def test_image_runs_coverage_offline(tmp_path, minirepo_spec_and_repo):
    from skeptic.image import ensure_repo_image
    from skeptic.workspace import materialize

    spec, repo_dir = minirepo_spec_and_repo
    pristine = tmp_path / "pristine"
    materialize(repo_dir, spec.repo.commit, pristine)
    ref = ensure_repo_image(spec, pristine, tmp_path / "img")
    result = subprocess.run(
        ["docker", "run", "--rm", "--network", "none", ref.tag,
         "python", "-m", "coverage", "--version"],
        capture_output=True, text=True, timeout=60, check=False,
    )
    assert result.returncode == 0


@pytest.mark.docker
@pytest.mark.slow
def test_constraints_pin_coverage(tmp_path, minirepo_spec_and_repo):
    from skeptic.image import ensure_repo_image
    from skeptic.workspace import materialize

    spec, repo_dir = minirepo_spec_and_repo
    pristine = tmp_path / "pristine"
    materialize(repo_dir, spec.repo.commit, pristine)
    ref = ensure_repo_image(spec, pristine, tmp_path / "img")
    assert "coverage==" in ref.constraints_path.read_text()


def test_tag_slug_emits_a_legal_tag_name_component():
    """`verify --diff` builds an image for whatever directory the caller
    points at. The registry grammar is `[a-z0-9]+((\\.|_|__|-+)[a-z0-9]+)*`,
    so substitution alone is not enough: "My Repo!" would come out
    "my-repo-", which `docker build -t` rejects on a tag Skeptic built for
    itself. Separator runs collapse, leading and trailing separators go, and
    a name with no alphanumeric character at all falls back to "repo"."""
    from skeptic.image import tag_slug

    assert tag_slug("MyRepo") == "myrepo"
    assert tag_slug("my repo!") == "my-repo"
    assert tag_slug("keep.me_-1") == "keep.me_-1"
    assert tag_slug(".hidden") == "hidden"
    assert tag_slug("trailing-") == "trailing"
    assert tag_slug("a  b") == "a-b"
    assert tag_slug("!!!") == "repo"


def test_corpus_image_tags_are_unchanged_by_slug_sanitization():
    """The slug rule landed for the diff lane's arbitrary repo names. Both
    corpus slugs are already tag-safe, so no cached image moves and no
    corpus measurement rebuilds. These two values pin that: a future change
    to the slug rule (or to the Dockerfile the tag hashes) shows up here
    rather than as a silent 90-second rebuild per repo."""
    from pathlib import Path

    from skeptic.spec import list_tasks

    tags = {spec.task_id: repo_image_tag(spec)
            for spec in list_tasks(Path(__file__).parent.parent / "tasks")}
    assert tags["click-0001"] == "skeptic-repo-click:5aa8ac43527f-1ba53db3"
    assert tags["rich-0001"] == "skeptic-repo-rich:9d8f9a372cc5-1ed41059"


def test_live_image_digest_reads_the_daemon_and_survives_its_absence(monkeypatch):
    """`_image_id` calls this on every manifest build, and the fast lane runs
    on machines with no docker installed, so a missing binary has to read as
    "cannot answer" rather than raise. An unknown tag exits non-zero and reads
    the same way; only a clean exit yields a digest."""
    def fake_run(argv, **kwargs):
        assert argv[:4] == ["docker", "image", "inspect", "--format"]
        return subprocess.CompletedProcess(argv, 0, stdout="sha256:abc123\n", stderr="")

    monkeypatch.setattr("skeptic.image.subprocess.run", fake_run)
    assert live_image_digest("skeptic-repo-click:tag") == "sha256:abc123"

    monkeypatch.setattr("skeptic.image.subprocess.run", lambda argv, **kw:
                        subprocess.CompletedProcess(argv, 1, stdout="", stderr="No such image"))
    assert live_image_digest("skeptic-repo-click:missing") is None

    def no_binary(*args, **kwargs):
        raise FileNotFoundError("docker")

    monkeypatch.setattr("skeptic.image.subprocess.run", no_binary)
    assert live_image_digest("skeptic-repo-click:tag") is None
