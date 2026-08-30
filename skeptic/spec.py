from __future__ import annotations

import shlex
from pathlib import Path, PurePosixPath, PureWindowsPath
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


def normalize_ro_subpath(raw: str) -> str:
    """Validate and normalize a protected workspace-relative mount path."""
    posix = PurePosixPath(raw)
    windows = PureWindowsPath(raw)
    if (
        not raw
        or posix.is_absolute()
        or windows.is_absolute()
        or bool(windows.drive)
        or ".." in posix.parts
        or ".." in windows.parts
    ):
        raise ValueError(
            f"protected read-only mount path {raw!r} must be a non-empty "
            f"repository-relative path with no '..' component"
        )
    normalized = str(posix)
    if normalized == ".":
        raise ValueError(
            f"protected read-only mount path {raw!r} names the workspace root; "
            f"it must name a path strictly beneath the workspace"
        )
    return normalized


class EnvironmentSpec(_Model):
    install: list[str]
    # A pip constraints file, relative to the checkout like `seed.bug_patch`:
    # the frozen closure every install path (image resolve stage, venv lane)
    # pins to, so a fresh machine measures what the corpus measured (row 225:
    # unpinned installs turned 8 rich tests red on any tree once pygments
    # moved). None leaves every install unpinned, which the diff lane keeps.
    constraints: str | None = None
    test_cmd: str
    test_dirs: list[str]
    config_files: list[str] = []
    src_dirs: list[str]
    golden_dirs: list[str] = []
    timeout_s: int
    network_after_install: bool = False

    @model_validator(mode="after")
    def _protected_mount_subpaths_are_relative(self) -> EnvironmentSpec:
        for field in ("test_dirs", "config_files", "golden_dirs"):
            for raw in getattr(self, field):
                try:
                    normalize_ro_subpath(raw)
                except ValueError as exc:
                    raise ValueError(f"environment.{field} entry is invalid: {exc}") from exc
        return self

    @property
    def constraints_file(self) -> Path | None:
        return Path(self.constraints) if self.constraints else None

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
    # None is the `verify --diff` posture: the baseline is the pristine tree
    # at the audited base commit, so there is no bug to inject and no patch
    # to name. `git apply` exits 128 on an empty patch, so a placeholder file
    # could not stand in for the absent one, and every reader on the verify
    # path branches on None instead (cli._verify_cache_key, cli.do_verify,
    # collector._baseline_key, collector._apply_seed).
    bug_patch: str | None = None
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
    variants: list[VariantSpec]
    expected: ExpectedSpec

    @model_validator(mode="after")
    def _require_clean_variant(self) -> EvaluationSpec:
        # A synthesized `verify --diff` spec declares no variants at all:
        # there is no corpus task behind it, so there is no gold patch to
        # restore the baseline with and nothing to check it against. A spec
        # that does declare variants still needs its clean one.
        if self.variants and not any(v.label == "clean" for v in self.variants):
            raise ValueError(
                "evaluation.variants must include at least one variant with "
                "label: clean (the gold patch). Skeptic checks "
                "gold-restores-baseline against it, so a task without a clean "
                "variant cannot be admitted."
            )
        return self


class AcceptanceSuiteSpec(_Model):
    path: str
    must_pass_on: list[str]
    must_fail_on: list[str]


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
    acceptance_suite: AcceptanceSuiteSpec | None = None

    @model_validator(mode="after")
    def _seed_required_when_variants_exist(self) -> TaskSpec:
        # `SeedSpec.bug_patch` is optional so the spec `verify --diff`
        # synthesizes can omit it, and that spec declares no variants. A
        # task that does declare variants is a corpus task: every variant
        # patch applies on top of the seeded tree, `seed --check`
        # materializes that tree from this patch, and BUILD reads it too. A
        # yaml that omits it would otherwise die with a bare TypeError deep
        # in a run instead of failing at load.
        if self.evaluation.variants and self.seed.bug_patch is None:
            raise ValueError(
                "seed.bug_patch is required for a task that declares "
                "evaluation.variants: the variant patches apply on top of "
                "the seeded tree and `seed --check` builds that tree from "
                "this patch. Only a spec with no variants (the one "
                "`verify --diff` synthesizes, whose baseline is the pristine "
                "tree at the audited commit) may omit it. Next: add "
                "seed.bug_patch to the task file."
            )
        return self

    @model_validator(mode="after")
    def _acceptance_names_resolve(self) -> TaskSpec:
        if self.acceptance_suite is None:
            return self
        known = {"pristine", "seeded"} | {v.id for v in self.evaluation.variants}
        names = set(self.acceptance_suite.must_pass_on) | set(self.acceptance_suite.must_fail_on)
        unknown = sorted(names - known)
        if unknown:
            raise ValueError(
                f"acceptance_suite names {unknown} are neither 'pristine', "
                f"'seeded', nor a variant id from evaluation.variants. The "
                f"acceptance matrix runs against exactly those trees. Next: "
                f"fix the name or add the variant."
            )
        if "seeded" not in self.acceptance_suite.must_fail_on:
            raise ValueError(
                "acceptance_suite.must_fail_on must include 'seeded': a suite "
                "that does not fail the seeded tree cannot discriminate the "
                "bug it exists to pin (plan invariant 5)."
            )
        both = sorted(set(self.acceptance_suite.must_pass_on)
                      & set(self.acceptance_suite.must_fail_on))
        if both:
            raise ValueError(
                f"acceptance_suite lists {both} in both must_pass_on and "
                f"must_fail_on. The matrix would materialize that tree twice "
                f"and one branch would always fail, reporting a corpus bug as "
                f"an invariant failure. Next: decide which side the tree "
                f"belongs on."
            )
        hacked = {v.id for v in self.evaluation.variants if v.label == "hacked"}
        missing = sorted(hacked - set(self.acceptance_suite.must_fail_on))
        if missing:
            raise ValueError(
                f"acceptance_suite.must_fail_on is missing hacked variants "
                f"{missing}. A hack does not fix the bug (a patch that does "
                f"is a gold-prime), and the acceptance suite is the frozen "
                f"statement of the bug being fixed, so the suite must be red "
                f"on every hack. Eval B's GREEN-wrong classification rests on "
                f"exactly that. If a hack really does pass the suite, the "
                f"suite is too weak or the variant is mislabeled. Next: add "
                f"{missing} to must_fail_on and re-run `seed --check`."
            )
        return self


def load_task(path: Path) -> TaskSpec:
    if not path.is_file():
        raise SkepticInfraError(
            f"Task spec not found: {path}. Skeptic needs the YAML spec to know "
            f"what to run. Next: `skeptic tasks` to see available task ids."
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
            f"Next: `skeptic tasks`."
        )
    return load_task(candidate)


def list_tasks(tasks_dir: Path) -> list[TaskSpec]:
    """Load every task spec under `tasks_dir`, sorted by task_id.

    A yaml that fails validation raises `load_task`'s own worded error here
    rather than being silently skipped: a listing command that drops a
    corrupt spec on the floor hides exactly the corpus bug the validator
    exists to surface, and `seed --check` on that task would hit the same
    raise anyway.
    """
    specs = [load_task(path) for path in tasks_dir.glob("*.yaml")]
    return sorted(specs, key=lambda spec: spec.task_id)
