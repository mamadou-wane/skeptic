from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from skeptic.errors import SkepticInfraError


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RepoSpec(_Model):
    url: str
    commit: str
    language: Literal["python"]
    python: str


class EnvironmentSpec(_Model):
    install: list[str]
    test_cmd: str
    test_dirs: list[str]
    src_dirs: list[str]
    golden_dirs: list[str] = []
    timeout_s: int
    network_after_install: bool = False


class SeedSpec(_Model):
    bug_patch: str
    failing_tests: list[str]
    notes_private: str = ""


class BuilderInputSpec(_Model):
    problem_statement: str
    hints: list[str] = []
    allowed_paths: list[str]


class ConstraintsSpec(_Model):
    max_iterations: int
    token_budget: int
    cost_ceiling_usd: float


class MutationSpec(_Model):
    budget_mutants: int
    scope: Literal["patch_only", "patch_plus_callers"]


class AdversarialSpec(_Model):
    n_candidates: int


class VerificationSpec(_Model):
    patch_coverage_min: float
    mutation: MutationSpec
    adversarial_tests: AdversarialSpec


class VariantSpec(_Model):
    id: str
    patch: str
    label: Literal["clean", "hacked"]
    hack_category: str | None = None


class ExpectedSpec(_Model):
    clean_verdict: Literal["PASS"]
    hacked_verdict_any_of: list[Literal["SUSPECT", "FAIL"]]


class EvaluationSpec(_Model):
    acceptance_tests: str | None = None
    variants: list[VariantSpec]
    expected: ExpectedSpec


class TaskSpec(_Model):
    schema_version: Literal[1]
    task_id: str
    repo: RepoSpec
    environment: EnvironmentSpec
    seed: SeedSpec
    builder_input: BuilderInputSpec
    constraints: ConstraintsSpec
    verification: VerificationSpec
    evaluation: EvaluationSpec


def load_task(path: Path) -> TaskSpec:
    if not path.is_file():
        raise SkepticInfraError(
            f"Task spec not found: {path}. Skeptic needs the YAML spec to know "
            f"what to run. Next: `skeptic tasks list` to see available task ids."
        )
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise SkepticInfraError(
            f"Task spec {path} is not valid YAML ({exc}). Fix the file, then "
            f"re-run `skeptic seed --task <id> --check`."
        ) from exc
    try:
        return TaskSpec.model_validate(data)
    except ValidationError as exc:
        raise SkepticInfraError(
            f"Task spec {path} failed validation:\n{exc}\n"
            f"Fix the fields above, then re-run `skeptic seed --task <id> --check`."
        ) from exc


def find_task(task_id: str, tasks_dir: Path) -> TaskSpec:
    candidate = tasks_dir / f"{task_id}.yaml"
    if not candidate.is_file():
        known = sorted(p.stem for p in tasks_dir.glob("*.yaml"))
        raise SkepticInfraError(
            f"No task named {task_id!r} in {tasks_dir} (known: {known or 'none'}). "
            f"Skeptic resolves --task <id> to <tasks_dir>/<id>.yaml. "
            f"Next: `skeptic tasks list`."
        )
    return load_task(candidate)
