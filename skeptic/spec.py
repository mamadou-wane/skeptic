from __future__ import annotations

import shlex
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
#
# Quotes are not in this set: shlex.split and sh -c tokenize a quoted
# argument the same way (`python -m pytest -q -k "not slow"` yields the
# same argv both ways). Banning them would remove the only way to pass
# an argument containing a space, since test_cmd is a single string
# (2026-07-26 review finding 4).
_TEST_CMD_METACHARS = set(";&|<>$`(){}[]*?!~#\\\n\r\t")


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
        try:
            shlex.split(cmd)
        except ValueError as exc:
            raise ValueError(
                f"environment.test_cmd {cmd!r} has unbalanced quoting ({exc}). "
                f"skeptic build tokenizes test_cmd with shlex.split before "
                f"running it as argv, so an unbalanced quote cannot be "
                f"tokenized at all; a per-iteration refusal at BUILD is where "
                f"this would otherwise surface, after API spend has started. "
                f"Next: fix the quoting in test_cmd, e.g. "
                f'`python -m pytest -q -k "not slow"`.'
            ) from exc
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
    # Known-flaky nodeids, excluded from every evidence rule in `t1_collect`
    # and `t1_outcomes`. M5 surface landed at M3 (DECISIONS.md #93): no M3
    # fixture is flaky and neither corpus task sets it, and `extra="forbid"`
    # means a task file cannot carry the key until the field exists, so this
    # is here to avoid a second spec bump rather than to serve a consumer
    # today. The 2x rerun-before-flag that would populate it automatically
    # runs in the collector and is deferred with it.
    quarantine: list[str] = []
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
    # Stratified sampling over (function x operator) strata is seeded, so a
    # mutation run repeats byte-for-byte; the VERIFY stage cache key consumes
    # this via model_dump.
    seed: int = 1337


class AdversarialSpec(_Model):
    n_candidates: int


class ProbeEntrypoint(_Model):
    call: str                       # dotted path to an importable callable
    args: list = []                 # YAML scalars only; reaches the driver as JSON
    kwargs: dict = {}

    @model_validator(mode="after")
    def _call_is_a_dotted_identifier_path(self) -> ProbeEntrypoint:
        parts = self.call.split(".")
        if len(parts) < 2 or not all(part.isidentifier() for part in parts):
            raise ValueError(
                f"consumer_probe entrypoint call {self.call!r} is not a "
                f"dotted path with at least two identifier parts. Skeptic's "
                f"probe driver builds `import a.b; a.b.c(...)` from this "
                f"string verbatim, so anything else would be code injection "
                f"into the driver. Next: write call as "
                f"`module.path.to_callable`, e.g. "
                f"`click.utils._make_default_short_help`."
            )
        return self


class ConsumerProbeSpec(_Model):
    entrypoints: list[ProbeEntrypoint] = []


class VerificationSpec(_Model):
    patch_coverage_min: float
    mutation: MutationSpec
    adversarial_tests: AdversarialSpec
    consumer_probe: ConsumerProbeSpec = ConsumerProbeSpec()


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
