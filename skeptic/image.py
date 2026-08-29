from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from skeptic.errors import SkepticInfraError
from skeptic.spec import TaskSpec
from skeptic.trace import config_hash

# Captured from `docker pull python:3.12-slim && docker inspect ...` on
# the Task 3 execution date. Digest-pinned per DECISIONS.md #65: a moving
# tag would silently change every measurement under the corpus.
BASE_IMAGE = "python@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de"

# Build backends preinstalled (and version-pinned via the freeze) so the
# session-start editable install can run --no-index --no-build-isolation:
# click uses flit_core, rich uses poetry-core, httpx uses hatchling, and
# setuptools/wheel cover the long tail.
_BUILD_BACKENDS = "flit_core poetry-core setuptools hatchling wheel"

# Harness measurement tooling: t1_coverage needs `coverage` inside the
# VERIFY container, and that container runs with --network none, so it
# cannot be installed after the image is built. It belongs in the image
# template rather than a task's environment.install, since the repo under
# test has no opinion about it. Installed in the resolve stage before the
# freeze, under the task's pin when one is declared, so `pip freeze` carries
# its version in constraints.txt alongside the repo's own dependencies
# (DECISIONS.md #82, #231).
_HARNESS_TOOLS = "coverage"

# Where a task's committed closure lands in the build context, and so under
# /src in the resolve stage. Host-side only: `ensure_repo_image` writes it
# beside the pristine export before the build and removes it after, and the
# resolve stage's layers never reach the final image.
CONSTRAINTS_IN_CONTEXT = ".skeptic-constraints.txt"

# What a docker tag's name component accepts, per the registry's own grammar.
_TAG_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789._-")


@dataclass(frozen=True)
class ImageRef:
    tag: str
    image_id: str
    constraints_path: Path


def tag_slug(name: str) -> str:
    """`name` reduced to a legal docker tag name component.

    The corpus feeds this repo URLs whose last segment is already a plain
    lowercase word (click, rich), but `verify --diff` builds an image for
    whatever local directory the caller points at, and a name component
    outside [a-z0-9._-] makes `docker build -t` fail on a tag Skeptic wrote
    for itself. Substitution alone is not enough: the registry grammar is
    `[a-z0-9]+((\\.|_|__|-+)[a-z0-9]+)*`, so a separator run, a leading dot
    or a trailing dash is rejected too, and "My Repo!" would substitute to
    "my-repo-" and still fail the build. Hence the collapse, the trim, and
    the "repo" fallback for a name with no alphanumeric character at all.
    Verified a no-op for both corpus slugs: no cached image tag moves
    (tests/test_image.py pins the two values).
    """
    subbed = "".join(ch if ch in _TAG_CHARS else "-" for ch in name.lower())
    return re.sub(r"-{2,}", "-", subbed).strip("._-") or "repo"


def pinned_closure(spec: TaskSpec) -> str | None:
    """The task's committed closure as text, or None when it declares none.

    A declared file that is not there is an infra error rather than an
    unpinned build: the whole point of the pin is that a fresh checkout
    resolves the same closure the corpus measured, and a build that silently
    ran without it would carry the corpus tag over a different closure.
    """
    path = spec.environment.constraints_file
    if path is None:
        return None
    try:
        return path.read_text()
    except OSError as exc:
        raise SkepticInfraError(
            f"environment.constraints names {path}, which cannot be read "
            f"({exc.strerror}). Skeptic pins every task install to a committed "
            f"closure so a fresh machine measures what the corpus measured. "
            f"Next: restore the file from git, or drop the field to build "
            f"unpinned on purpose."
        ) from exc


def repo_image_tag(spec: TaskSpec) -> str:
    slug = tag_slug(spec.repo.url.rstrip("/").rsplit("/", 1)[-1])
    # The tag hashes the rendered Dockerfile, not just the install commands:
    # that also keys on BASE_IMAGE and the template itself, so a digest bump
    # or a template edit gets a new tag instead of silently reusing a stale
    # image (2026-07-26 review finding 1: a commit-only tag would survive
    # either change, and ensure_repo_image would then skip the build and the
    # stage cache would serve the previous result under the unchanged tag).
    # The closure's bytes ride in the same hash: the render only names the
    # file, and a moved pin is a different image.
    keyed: dict[str, str] = {"dockerfile": render_dockerfile(spec)}
    pinned = pinned_closure(spec)
    if pinned is not None:
        keyed["constraints"] = pinned
    env_hash = config_hash(keyed)[:8]
    return f"skeptic-repo-{slug}:{spec.repo.commit[:12]}-{env_hash}"


def render_dockerfile(spec: TaskSpec) -> str:
    install_lines = "\n".join(f"RUN {cmd}" for cmd in spec.environment.install)
    # PIP_CONSTRAINT reaches every pip call of the stage, the task's own
    # install lines and the backend and harness installs below alike. Set
    # only when a closure is declared, so an undeclared task renders the
    # pre-M7 text byte for byte and its tag stays put.
    if spec.environment.constraints:
        install_lines = f"ENV PIP_CONSTRAINT=/src/{CONSTRAINTS_IN_CONTEXT}\n" + install_lines
    return f"""\
# Stage 1 resolves the dependency closure against the pristine tree. Its
# layers never reach the final image, so no repo source ships in the image
# the Builder runs in.
FROM {BASE_IMAGE} AS resolve
WORKDIR /src
COPY . /src
{install_lines}
RUN pip install -q {_BUILD_BACKENDS}
RUN pip install -q {_HARNESS_TOOLS}
RUN pip freeze --exclude-editable > /constraints.txt

# Stage 2 is the runtime image: base interpreter plus the frozen closure,
# no source. Deps go into the base interpreter's site-packages on purpose:
# the session-start overlay venv (--system-site-packages) chains to the
# base interpreter, so this is the only place it can see them from.
FROM {BASE_IMAGE}
COPY --from=resolve /constraints.txt /opt/constraints.txt
RUN pip install -q --no-cache-dir -r /opt/constraints.txt
"""


def _docker(args: list[str], timeout_s: int = 1800) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=timeout_s, check=False
    )


def live_image_digest(tag: str) -> str | None:
    """The digest the daemon resolves `tag` to right now, or None when it
    cannot answer: no docker binary (the fast test lane runs without one) or
    the tag absent locally.

    A tag is not a digest. `repo_image_tag` hashes the rendered Dockerfile and,
    for a pinned task, the closure's bytes; an unpinned spec, the diff lane's
    shape, still names a different dependency closure depending on when it was
    built, which is how the rich image once carried pygments 2.20.0 where a
    rebuild resolved 2.21.0 and reds eight rendering tests on any tree (row
    225). Anything recording provenance wants the digest, and the tag it
    computed is the only handle it has to ask for.
    """
    try:
        inspect = _docker(["image", "inspect", "--format", "{{.Id}}", tag], timeout_s=30)
    except FileNotFoundError:
        return None
    if inspect.returncode != 0:
        return None
    return inspect.stdout.strip() or None


def ensure_repo_image(spec: TaskSpec, pristine_dir: Path, workdir: Path) -> ImageRef:
    """Build (or reuse) the per-repo deps image; return its content-addressed id.

    pristine_dir is the build context: a materialized `git archive` export of
    the pinned commit. It is host-side only and the caller deletes it after
    the build; the final image contains the frozen constraints and no source.
    """
    tag = repo_image_tag(spec)
    pinned = pinned_closure(spec)
    constraints_path = workdir / "constraints.txt"
    inspect = _docker(["image", "inspect", "--format", "{{.Id}}", tag], timeout_s=30)
    built = inspect.returncode != 0
    if built:
        workdir.mkdir(parents=True, exist_ok=True)
        dockerfile = workdir / "Dockerfile"
        dockerfile.write_text(render_dockerfile(spec))
        context_pin = pristine_dir / CONSTRAINTS_IN_CONTEXT
        if pinned is not None:
            context_pin.write_text(pinned)
        try:
            build = _docker(["build", "-t", tag, "-f", str(dockerfile), str(pristine_dir)])
        finally:
            if pinned is not None:
                context_pin.unlink(missing_ok=True)
        if build.returncode != 0:
            raise SkepticInfraError(
                f"docker build failed for {tag} (exit {build.returncode}).\n"
                f"stderr tail:\n{build.stderr[-2000:]}\n"
                f"Skeptic prebuilds one deps-only image per repo commit so "
                f"variant runs pay zero installs. Next: check the install "
                f"commands in the task spec, or run the docker build by hand "
                f"with the Dockerfile at {dockerfile}."
            )
        inspect = _docker(["image", "inspect", "--format", "{{.Id}}", tag], timeout_s=30)
        if inspect.returncode != 0:
            raise SkepticInfraError(
                f"docker image inspect failed for {tag} right after a "
                f"successful build (exit {inspect.returncode}): "
                f"{inspect.stderr[-500:]}\n"
                f"Skeptic records the image id in the run manifest for "
                f"reproducibility. Next: run `docker image inspect {tag}` "
                f"by hand."
            )
    # A fresh build always reads its freeze back, so a tag that moved (a new
    # pin, a template edit) refreshes constraints.txt instead of leaving the
    # previous image's closure on disk under the new tag.
    if built or not constraints_path.is_file():
        cat = _docker(["run", "--rm", "--network", "none", tag,
                       "cat", "/opt/constraints.txt"], timeout_s=120)
        if cat.returncode != 0:
            raise SkepticInfraError(
                f"could not read /opt/constraints.txt from {tag} "
                f"(exit {cat.returncode}): {cat.stderr[-500:]}\n"
                f"Skeptic commits the frozen dependency closure as the "
                f"reproducibility lock. Next: rebuild the image "
                f"(`docker rmi {tag}`, then re-run)."
            )
        if pinned is not None and cat.stdout != pinned:
            pin_path = spec.environment.constraints_file
            drift = sorted(set(cat.stdout.splitlines()) ^ set(pinned.splitlines()))
            raise SkepticInfraError(
                f"the closure {tag} resolved differs from the committed pin "
                f"{pin_path}: {', '.join(drift[:8])}"
                f"{', ...' if len(drift) > 8 else ''}.\n"
                f"Skeptic pins task installs so a fresh machine measures what "
                f"the corpus measured, and a pin the resolver did not honor "
                f"is not a pin, and the same build drifts the same way every "
                f"time. Next: if the closure should move, write this image's "
                f"freeze over the pin (`docker run --rm {tag} cat "
                f"/opt/constraints.txt > {pin_path}`) and record the move in "
                f"DECISIONS.md; otherwise fix the install lines the pin does "
                f"not cover."
            )
        workdir.mkdir(parents=True, exist_ok=True)
        constraints_path.write_text(cat.stdout)
    return ImageRef(tag=tag, image_id=inspect.stdout.strip(),
                    constraints_path=constraints_path)
