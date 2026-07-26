from __future__ import annotations

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


@dataclass(frozen=True)
class ImageRef:
    tag: str
    image_id: str
    constraints_path: Path


def repo_image_tag(spec: TaskSpec) -> str:
    slug = spec.repo.url.rstrip("/").rsplit("/", 1)[-1]
    # The tag keys on everything that shaped the image: change the install
    # commands or the backend list and the tag changes too, so a stale image
    # is never silently reused (review finding: a commit-only tag would
    # survive an install-command edit).
    env_hash = config_hash({"install": spec.environment.install,
                            "backends": _BUILD_BACKENDS})[:8]
    return f"skeptic-repo-{slug}:{spec.repo.commit[:12]}-{env_hash}"


def render_dockerfile(spec: TaskSpec) -> str:
    install_lines = "\n".join(f"RUN {cmd}" for cmd in spec.environment.install)
    return f"""\
# Stage 1 resolves the dependency closure against the pristine tree. Its
# layers never reach the final image, so no repo source ships in the image
# the Builder runs in.
FROM {BASE_IMAGE} AS resolve
WORKDIR /src
COPY . /src
{install_lines}
RUN pip install -q {_BUILD_BACKENDS}
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


def ensure_repo_image(spec: TaskSpec, pristine_dir: Path, workdir: Path) -> ImageRef:
    """Build (or reuse) the per-repo deps image; return its content-addressed id.

    pristine_dir is the build context: a materialized `git archive` export of
    the pinned commit. It is host-side only and the caller deletes it after
    the build; the final image contains the frozen constraints and no source.
    """
    tag = repo_image_tag(spec)
    constraints_path = workdir / "constraints.txt"
    inspect = _docker(["image", "inspect", "--format", "{{.Id}}", tag], timeout_s=30)
    if inspect.returncode != 0:
        workdir.mkdir(parents=True, exist_ok=True)
        dockerfile = workdir / "Dockerfile"
        dockerfile.write_text(render_dockerfile(spec))
        build = _docker(["build", "-t", tag, "-f", str(dockerfile), str(pristine_dir)])
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
    if not constraints_path.is_file():
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
        workdir.mkdir(parents=True, exist_ok=True)
        constraints_path.write_text(cat.stdout)
    return ImageRef(tag=tag, image_id=inspect.stdout.strip(),
                    constraints_path=constraints_path)
