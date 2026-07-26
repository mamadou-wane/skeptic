from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from skeptic.errors import SkepticInfraError


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RepoSpec(_Model):
    url: str
    commit: str
    language: Literal["python"]
    python: str


# Admission (`seed --check`) runs test_cmd through `sh -c`; `skeptic build`
# runs it through `shlex.split` + exec_argv (no shell). The two agree on a
# plain argv command and diverge on anything shell-flavored: reject at spec
# load, before BUILD spends money discovering the divergence with an image
# already built (2026-07-26 review finding 6; DECISIONS.md #72).
_TEST_CMD_METACHARS = set(";&|<>$`(){}[]*?!~#\\\"'\n\r\t")


class EnvironmentSpec(_Model):
    install: list[str]
    test_cmd: str
    test_dirs: list[str]
    config_files: list[str] = []
    src_dirs: list[str]
    golden_dirs: list[str] = []
    timeout_s: int
    network_after_install: bool = False

    @model_validator(mode="after")
    def _test_cmd_is_argv_safe(self) -> EnvironmentSpec:
        cmd = self.test_cmd
        if any(ch in _TEST_CMD_METACHARS for ch in cmd):
            raise ValueError(
                f"environment.test_cmd {cmd!r} contains a shell "
                f"metacharacter. skeptic build runs test_cmd as argv "
                f"(shlex.split, no shell), so &&, a pipe, a redirect, or a "
                f"glob would pass `seed --check` (which runs it under "
                f"sh -c) and only fail once BUILD's image is already built "
                f"and API spend has started. Next: rewrite test_cmd as a "
                f"plain argv command with no shell syntax, e.g. "
                f"`python -m pytest -q`."
            )
        first = cmd.split(" ", 1)[0] if cmd else ""
        if "=" in first:
            raise ValueError(
                f"environment.test_cmd {cmd!r} starts with what looks like "
                f"an environment-variable assignment ({first!r}). skeptic "
                f"build runs test_cmd as argv (shlex.split, no shell), so "
                f"that token is executed as the binary name instead of "
                f"being interpreted as an assignment. Next: move the "
                f"assignment into environment.install, or drop it from "
                f"test_cmd."
            )
        return self


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

    @model_validator(mode="after")
    def _require_clean_variant(self) -> EvaluationSpec:
        if not any(v.label == "clean" for v in self.variants):
            raise ValueError(
                "evaluation.variants must include at least one variant with "
                "label: clean (the gold patch). Skeptic checks "
                "gold-restores-baseline against it, so a task without a clean "
                "variant cannot be admitted."
            )
        return self


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
            f"Task spec {path} is not valid YAML ({exc}). Skeptic needs valid "
            f"YAML to parse the file into a TaskSpec before it can run "
            f"anything. Fix the file, then re-run "
            f"`skeptic seed --task <id> --check`."
        ) from exc
    try:
        return TaskSpec.model_validate(data)
    except ValidationError as exc:
        raise SkepticInfraError(
            f"Task spec {path} failed validation:\n{exc}\n"
            f"Skeptic needs every required field present and correctly typed "
            f"before it can run the task. Fix the fields above, then re-run "
            f"`skeptic seed --task <id> --check`."
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
