"""Docker-only suite execution for agent-produced candidate patches.

Trusted corpus admission owns its host runner in seedcheck. These entry points
accept no runner or runner factory, and never fall back to host execution.
"""
import shlex
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from skeptic.artifacts import STRUCTURED_MAX, ArtifactSpec, read_artifact_bytes
from skeptic.candidate import snapshot
from skeptic.collector import _run_private_phase
from skeptic.errors import SkepticInfraError
from skeptic.image import ensure_repo_image
from skeptic.sandbox import HostDeadline, RunContainer
from skeptic.seedcheck import SuiteResult, _drop_quarantined, parse_junit_bytes
from skeptic.spec import TaskSpec
from skeptic.workspace import apply_candidate, apply_patch, materialize


@dataclass(frozen=True)
class CandidateSuiteResult:
    suite: SuiteResult
    artifacts: Path
    image_id: str


def run_candidate_suite(spec: TaskSpec, repo: Path, patch: Path,
                        workdir: Path) -> CandidateSuiteResult:
    return _run_suite(spec, repo, patch, workdir, acceptance=False)


def run_candidate_acceptance(spec: TaskSpec, repo: Path, patch: Path,
                             workdir: Path) -> CandidateSuiteResult:
    if spec.acceptance_suite is None:
        raise SkepticInfraError(
            f"{spec.task_id} declares no acceptance_suite. "
            "Next: provide a frozen acceptance suite before classifying a candidate."
        )
    return _run_suite(spec, repo, patch, workdir, acceptance=True)


def _run_suite(spec: TaskSpec, repo: Path, patch: Path, workdir: Path,
               *, acceptance: bool) -> CandidateSuiteResult:
    workdir.mkdir(parents=True, exist_ok=True)
    # Evidence outlives the disposable tree. Each invocation has a fresh root;
    # no later candidate receives it as a writable mount.
    artifacts = Path(tempfile.mkdtemp(prefix="observed-", dir=workdir))
    with tempfile.TemporaryDirectory(prefix="execution-", dir=workdir) as scratch:
        scratch = Path(scratch)
        pristine = materialize(repo, spec.repo.commit, scratch / "pristine")
        try:
            image = ensure_repo_image(spec, pristine, workdir / "image")
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            raise SkepticInfraError(
                f"Docker image preparation did not complete ({type(exc).__name__}). "
                "Next: check Docker availability and retry the candidate evaluation."
            ) from exc
        tree = materialize(repo, spec.repo.commit, scratch / "candidate")
        if spec.seed.bug_patch is not None:
            apply_patch(tree, Path(spec.seed.bug_patch))
        apply_candidate(tree, patch, authored=True)
        ro = (tuple(spec.environment.test_dirs) + tuple(spec.environment.config_files)
              + tuple(spec.environment.golden_dirs))
        command = shlex.split(spec.environment.test_cmd)
        if acceptance:
            target = tree / ".skeptic-acceptance"
            if target.exists() or target.is_symlink():
                raise SkepticInfraError(
                    "Candidate occupies the reserved acceptance input path. "
                    "Next: remove that candidate change before acceptance evaluation."
                )
            snapshot(Path(spec.acceptance_suite.path), target)
            ro += (".skeptic-acceptance",)
            command = ["python", "-m", "pytest", "-q", ".skeptic-acceptance"]
        command += ["--junitxml=/tmp/skeptic-artifacts/junit.xml", "-o", "junit_family=xunit1"]
        deadline = HostDeadline.after(spec.environment.timeout_s)
        result = _run_private_phase(
            container=RunContainer(image.image_id, tree, ro_subpaths=ro, missing_ro="drop"),
            script=shlex.join(command), quarantine=scratch / "quarantine", sealed=artifacts,
            output_specs=(ArtifactSpec("junit.xml", STRUCTURED_MAX, required=False),),
            timeout_s=spec.environment.timeout_s, output_prefix="suite.", deadline=deadline,
        )
        if result.exit_code not in (0, 1):
            raise SkepticInfraError(
                f"Candidate suite exited {result.exit_code}; no completed suite is available. "
                f"Next: inspect {artifacts}/suite.err and retry the candidate evaluation."
            )
        data = read_artifact_bytes(artifacts, "junit.xml", STRUCTURED_MAX)
        try:
            suite = parse_junit_bytes(data, str(artifacts / "junit.xml"))
        except SkepticInfraError:
            raise
        except Exception as exc:
            raise SkepticInfraError(
                f"Candidate suite produced malformed JUnit ({type(exc).__name__}). "
                f"Next: inspect {artifacts}/junit.xml and retry the evaluation."
            ) from exc
        deadline.require_active("candidate suite read-back")
    deadline.require_active("candidate suite cleanup")
    return CandidateSuiteResult(_drop_quarantined(suite, spec.seed.quarantine),
                                artifacts, image.image_id)
