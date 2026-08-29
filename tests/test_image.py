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


def test_corpus_image_tags_are_unchanged_by_slug_sanitization(monkeypatch):
    """The slug rule landed for the diff lane's arbitrary repo names. Both
    corpus slugs are already tag-safe, so no cached image moves and no
    corpus measurement rebuilds. These two values pin that: a future change
    to the slug rule (or to the Dockerfile the tag hashes) shows up here
    rather than as a silent 90-second rebuild per repo."""
    from pathlib import Path

    from skeptic.spec import list_tasks

    root = Path(__file__).parent.parent
    monkeypatch.chdir(root)   # the pin is read off cwd, like seed.bug_patch
    tags = {spec.task_id: repo_image_tag(spec) for spec in list_tasks(root / "tasks")}
    # Moved once, on purpose: the M7 pin (DECISIONS row 231) put a committed
    # closure into the render and the tag, so both corpus images rebuilt to
    # the same closure the published runs measured.
    assert tags["click-0001"] == "skeptic-repo-click:5aa8ac43527f-5093884b"
    assert tags["rich-0001"] == "skeptic-repo-rich:9d8f9a372cc5-9e95a840"


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


# --- the committed closure (M7, DECISIONS row 231) ---------------------------


def _pinned(spec, path):
    return spec.model_copy(update={
        "environment": spec.environment.model_copy(update={"constraints": str(path)})})


def test_render_dockerfile_pins_the_resolve_stage_only_when_constraints_are_declared(tmp_path):
    """A declared closure reaches every pip call of the resolve stage through
    PIP_CONSTRAINT, set before the first install line; the freeze and the
    final stage are untouched. An undeclared one renders the pre-M7 text
    byte for byte, so the diff lane's and the minirepo's tags do not move."""
    from skeptic.image import CONSTRAINTS_IN_CONTEXT

    spec = make_task_spec()
    plain = render_dockerfile(spec)
    assert "PIP_CONSTRAINT" not in plain

    pin = tmp_path / "pins.txt"
    pin.write_text("pytest==9.1.1\n")
    pinned = render_dockerfile(_pinned(spec, pin))
    resolve, final = pinned.split("FROM " + BASE_IMAGE)[1:]
    env_line = f"ENV PIP_CONSTRAINT=/src/{CONSTRAINTS_IN_CONTEXT}"
    assert env_line in resolve
    assert resolve.index(env_line) < resolve.index(spec.environment.install[0])
    assert "PIP_CONSTRAINT" not in final
    assert "pip freeze --exclude-editable" in resolve


def test_repo_image_tag_keys_on_the_closure_text(tmp_path):
    """The tag hashes the closure's bytes, not its path: a moved pin is a
    different image, and a missing pin is an infra error naming the path
    rather than a build that silently runs unpinned."""
    from skeptic.errors import SkepticInfraError

    spec = make_task_spec()
    pin = tmp_path / "pins.txt"
    pin.write_text("pytest==9.1.1\n")
    pinned = repo_image_tag(_pinned(spec, pin))
    assert pinned != repo_image_tag(spec)
    pin.write_text("pytest==9.1.0\n")
    assert repo_image_tag(_pinned(spec, pin)) != pinned
    with pytest.raises(SkepticInfraError, match="missing.txt"):
        repo_image_tag(_pinned(spec, tmp_path / "missing.txt"))


def test_every_corpus_task_declares_a_committed_closure():
    """Row 225: the venv lane is not reproducible on a fresh machine until the
    task installs pin their transitive deps. One closure per repo, read out of
    the exact image the published runs measured, and every task names it."""
    from pathlib import Path

    from skeptic.spec import list_tasks

    root = Path(__file__).parent.parent
    by_repo: dict[str, set[str]] = {}
    for spec in list_tasks(root / "tasks"):
        assert spec.environment.constraints, f"{spec.task_id} declares no closure"
        pin = root / spec.environment.constraints
        assert pin.is_file() and pin.read_text().strip(), f"{pin} is missing or empty"
        by_repo.setdefault(spec.repo.url, set()).add(spec.environment.constraints)
    assert by_repo == {
        "https://github.com/pallets/click": {"constraints/click.txt"},
        "https://github.com/Textualize/rich": {"constraints/rich.txt"},
    }
    for pin in ("constraints/click.txt", "constraints/rich.txt"):
        lines = (root / pin).read_text().splitlines()
        assert all("==" in line for line in lines), f"{pin} carries an unpinned line"
        assert lines == sorted(lines, key=str.lower), f"{pin} is not in pip freeze order"


@pytest.mark.docker
@pytest.mark.slow
def test_ensure_repo_image_installs_the_pin_and_refuses_drift(tmp_path, minirepo_spec_and_repo):
    """Built under the pin, the image's freeze equals the pin byte for byte
    and is written back as constraints.txt. A pin that does not cover the
    closure (here, missing the harness's own `coverage`) is drift, and drift
    is an infra error naming the file, never a quiet rebuild under a
    different closure."""
    from skeptic.errors import SkepticInfraError
    from skeptic.image import ensure_repo_image
    from skeptic.workspace import materialize

    spec, repo_dir = minirepo_spec_and_repo
    pristine = tmp_path / "pristine"
    materialize(repo_dir, spec.repo.commit, pristine)
    frozen = ensure_repo_image(spec, pristine, tmp_path / "img").constraints_path.read_text()

    pin = tmp_path / "pin.txt"
    pin.write_text(frozen)
    ref = ensure_repo_image(_pinned(spec, pin), pristine, tmp_path / "img-pinned")
    assert ref.constraints_path.read_text() == frozen
    assert not (pristine / ".skeptic-constraints.txt").exists(), "the context copy is cleaned up"

    short = tmp_path / "short.txt"
    short.write_text("".join(line for line in frozen.splitlines(keepends=True)
                             if not line.startswith("coverage==")))
    with pytest.raises(SkepticInfraError, match="short.txt"):
        ensure_repo_image(_pinned(spec, short), pristine, tmp_path / "img-short")
