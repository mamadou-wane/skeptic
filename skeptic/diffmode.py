"""Spec synthesis for `verify --diff`: the audit path with no task spec.

`verify --task` reads everything it needs from a corpus yaml. `verify --diff`
is pointed at a working clone and a patch file and has no yaml at all, so
this module builds the same `TaskSpec` shape out of the repo itself: the PEP
517 backend it declares, where its tests live, where its source lives, and
which config files sit at its root. A value the caller passed on the command
line always wins over an inferred one, and every value that reaches the run
is printed in the banner, because plan §5.7 is explicit that inverted
src/test directories silently invert patch coverage.

Inference that runs out of evidence is a refusal naming the flag to pass.
For `test_dirs` the last resort before that refusal is a tests/ or test/
directory at the root, and with neither one there the run stops rather than
guessing: a wrong `test_dirs` is the one error the audit cannot report on
itself, since every check that reads it would be reading the wrong half of
the tree.
"""
from __future__ import annotations

import configparser
import subprocess
import tomllib
from pathlib import Path

from pydantic import ValidationError

from skeptic.errors import SkepticInfraError
from skeptic.spec import TaskSpec

# The corpus convention (every task yaml carries the same pair), used as the
# default for a repo that installs like an ordinary Python package.
DEFAULT_INSTALL = ["pip install -q -e . pytest"]
DEFAULT_TEST_CMD = "python -m pytest -q"

_DEFAULT_BACKEND = "setuptools.build_meta"

# The image's resolve stage bakes flit_core, poetry-core, setuptools,
# hatchling and wheel (skeptic/image.py:_BUILD_BACKENDS), and the overlay
# install inside the sandbox runs `--no-index` under `--network none`, so a
# repo whose pyproject names any other backend cannot have its wheel built
# where the audit runs. Those five packages provide exactly these backend
# strings. `setuptools.build_meta:__legacy__` is setuptools' own shim for a
# project with no [build-system] table of its own and ships in the same
# package, so refusing it would be a refusal of a backend already in the
# image. A missing [build-system] table means setuptools too, by PEP 517's
# own default, and is supported for the same reason.
SUPPORTED_BACKENDS = frozenset({
    "setuptools.build_meta",
    "setuptools.build_meta:__legacy__",
    "flit_core.buildapi",
    "poetry.core.masonry.api",
    "hatchling.build",
})

# Read as `environment.config_files`, which is what `t1_config` compares
# across the two trees. Only root-level files: a nested conftest.py belongs
# to the directory it configures and reaches the checks through test_dirs.
_ROOT_CONFIGS = ("pyproject.toml", "setup.cfg", "pytest.ini", "tox.ini", "conftest.py")


def assert_working_clone(repo: Path) -> None:
    """Refuse anything that is not a git working clone.

    VERIFY materializes both judged trees with `git archive` out of a clone
    of this path, so a plain directory of source has nothing to archive and
    a bare repo has nothing for the caller to have diffed against.
    """
    if not (repo.is_dir() and (repo / ".git").exists()):
        raise SkepticInfraError(
            f"--repo {repo} is not a git working clone (no .git inside it). "
            f"Skeptic clones this path and exports the base commit with "
            f"`git archive` to build the two trees it compares, so it needs "
            f"real git history rather than a directory of source. Next: "
            f"point --repo at the checkout the diff was taken from."
        )


def resolve_base(repo: Path, base: str) -> str:
    """`base` resolved to a full commit sha inside `repo`, or a refusal.

    Resolved before any docker work: an audit pinned to a ref that moves (or
    that this clone has never heard of) is not reproducible, and the run dir
    is named after this sha.
    """
    proc = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}"],
        capture_output=True, text=True, check=False,
    )
    sha = proc.stdout.strip()
    if proc.returncode != 0 or not sha:
        raise SkepticInfraError(
            f"--base {base!r} does not resolve to a commit in {repo}. "
            f"Skeptic pins the audit to one commit and materializes the "
            f"baseline tree from it, so the ref has to exist in this clone. "
            f"Next: check the ref, or fetch it (a shallow clone often has no "
            f"merge base), then re-run."
        )
    return sha


def read_diff(diff: Path) -> bytes:
    """The audited patch's bytes, refusing an unreadable or empty file.

    `git apply` exits 128 on a file that carries no patches, which VERIFY
    would surface as an infra failure several minutes into a docker run.
    Reading it here turns that into a refusal before the first container.
    """
    if not diff.is_file():
        raise SkepticInfraError(
            f"No diff at {diff}: --diff needs a readable patch file to apply "
            f"over the base tree. Next: check the path, or regenerate the "
            f"patch with `git diff <base>...HEAD > {diff.name}`."
        )
    try:
        data = diff.read_bytes()
    except OSError as exc:
        raise SkepticInfraError(
            f"Could not read the diff at {diff}: {exc}. Skeptic applies these "
            f"bytes to the base tree to build the candidate. Next: check the "
            f"path and its permissions."
        ) from exc
    if not data.strip():
        raise SkepticInfraError(
            f"The diff at {diff} is empty. An empty patch has no candidate to "
            f"audit, and `git apply` exits 128 on one, so there is nothing to "
            f"verify. Next: regenerate the patch with `git diff <base>...HEAD` "
            f"and check that the two refs differ."
        )
    return data


def _pyproject(tree: Path) -> dict:
    """`pyproject.toml` at the tree root, parsed, or an empty table."""
    path = tree / "pyproject.toml"
    if not path.is_file():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise SkepticInfraError(
            f"pyproject.toml at the base commit is not valid TOML ({exc}). "
            f"Skeptic reads it for the build backend and for "
            f"[tool.pytest.ini_options] testpaths before it builds the image "
            f"the audit runs in. Next: fix the file at the commit --base "
            f"names, or point --base somewhere it parses."
        ) from exc


def backend_of(tree: Path) -> str:
    """The PEP 517 build backend the tree declares.

    No pyproject.toml, or one with no [build-system] table, means setuptools:
    that is PEP 517's own fallback and pip applies it to any repo old enough
    to predate the table.
    """
    table = _pyproject(tree).get("build-system", {})
    return table.get("build-backend", _DEFAULT_BACKEND)


def assert_python_project(tree: Path) -> str:
    """The metadata file pip installs the tree by, or a refusal naming the
    boundary.

    pip's own rule: a directory with neither `pyproject.toml` nor `setup.py`
    "does not appear to be a Python project", and the audit's session-start
    overlay install (`sandbox.overlay_install_cmd`) needs pip to install the
    tree. `setup.cfg` alone is not enough for pip and so not enough here. A
    repo outside this boundary (`AlexanderAlcazar/nexus_student_hub#1`:
    `requirements.txt`, `src/`, `tests/`, nothing pip can install) is
    refused before an image is built, with the boundary stated, rather than
    failing inside the resolve stage with pip's message buried in a build
    log. Dependency discovery for such repos is out of scope on purpose
    (DECISIONS row 235).
    """
    for name in ("pyproject.toml", "setup.py"):
        if (tree / name).is_file():
            return name
    beside = " `setup.cfg` alone is not one: pip needs one of the two beside it." \
        if (tree / "setup.cfg").is_file() else ""
    raise SkepticInfraError(
        f"Unsupported project: the base commit has no pyproject.toml and no "
        f"setup.py at the repo root, so pip does not consider it a Python "
        f"project and Skeptic cannot install it into the audit image.{beside} "
        f"Skeptic audits pytest-based Python repositories with package "
        f"metadata pip can install at the root (pyproject.toml or setup.py, "
        f"on a setuptools, flit-core, poetry-core or hatchling backend). "
        f"Next: add a pyproject.toml that installs the code under test (a "
        f"[project] table with a name is enough for setuptools), commit it "
        f"at the base, and re-run; a requirements.txt alone does not make the "
        f"tree installable."
    )


def assert_supported_backend(tree: Path) -> str:
    """The tree's backend, refusing one the audit image cannot build with."""
    backend = backend_of(tree)
    if backend not in SUPPORTED_BACKENDS:
        raise SkepticInfraError(
            f"Build backend {backend!r} is out of scope. The image Skeptic "
            f"builds for the audit bakes "
            f"{', '.join(sorted(SUPPORTED_BACKENDS))} and nothing else, and "
            f"the install inside the sandbox runs with `--no-index` under "
            f"`--network none`, so {backend!r} could not be fetched to build "
            f"the wheel. Next: audit a repo on one of those backends, or add "
            f"the package to _BUILD_BACKENDS in skeptic/image.py and rebuild."
        )
    return backend


def _as_dirs(value: list[str] | str | None) -> list[str]:
    """A testpaths value as a list of paths, from a TOML array or an ini string."""
    if value is None:
        return []
    if isinstance(value, str):
        return value.split()
    return [str(item) for item in value]


def _ini_testpaths(path: Path, section: str) -> list[str]:
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(path, encoding="utf-8")
    except (configparser.Error, UnicodeDecodeError):
        # A config file this cannot parse is one nothing can be inferred
        # from. `_infer_test_dirs` ends in a refusal naming --test-dir, so
        # falling through never turns an unread file into a silent guess.
        return []
    if not parser.has_option(section, "testpaths"):
        return []
    return _as_dirs(parser.get(section, "testpaths"))


def _infer_test_dirs(tree: Path) -> tuple[list[str], str]:
    """Where the repo says its tests live, and which source said so."""
    ini_options = _pyproject(tree).get("tool", {}).get("pytest", {}).get("ini_options", {})
    declared = _as_dirs(ini_options.get("testpaths"))
    if declared:
        return declared, "inferred from pyproject.toml [tool.pytest.ini_options] testpaths"
    for name, section in (("pytest.ini", "pytest"), ("setup.cfg", "tool:pytest")):
        declared = _ini_testpaths(tree / name, section)
        if declared:
            return declared, f"inferred from {name} [{section}] testpaths"
    for name in ("tests", "test"):
        if (tree / name).is_dir():
            # Trailing slash is the corpus convention; `checks._util.under`
            # strips it, so both spellings behave identically.
            return [f"{name}/"], f"inferred from the {name}/ directory at the repo root"
    raise SkepticInfraError(
        "Could not tell where the tests live at the base commit. Skeptic "
        "looked for testpaths in pyproject.toml, pytest.ini and setup.cfg, "
        "then for a tests/ or test/ directory at the repo root, and found "
        "none. Every check that reads a test edit reads this list, so a "
        "guess here would invert what the audit reports. Next: re-run with "
        "--test-dir (repeatable), e.g. `--test-dir tests/`."
    )


def _line(label: str, value: str, source: str) -> str:
    return f"  {label}: {value}  ({source})"


def infer_environment(
    tree: Path, install: list[str], test_cmd: str,
    src_dirs: list[str], test_dirs: list[str],
) -> tuple[dict, list[str]]:
    """An `EnvironmentSpec`-shaped dict for `tree`, plus the banner lines.

    `tree` is a materialized export of the base commit, never the caller's
    working tree: that clone may be dirty or checked out somewhere else, and
    the audit is pinned to the base commit alone.
    """
    if test_dirs:
        tests, tests_why = list(test_dirs), "passed"
    else:
        tests, tests_why = _infer_test_dirs(tree)

    if src_dirs:
        src, src_why = list(src_dirs), "passed"
    elif (tree / "src").is_dir():
        src, src_why = ["src/"], "inferred from the src/ directory at the repo root"
    else:
        # The flat layout: the root is the source root, and the test dirs
        # above are what keeps the suite out of the coverage denominator.
        src, src_why = ["."], "inferred: no src/ at the repo root"

    config = [name for name in _ROOT_CONFIGS if (tree / name).is_file()]

    environment = {
        "install": list(install),
        "test_cmd": test_cmd,
        "test_dirs": tests,
        "config_files": config,
        "src_dirs": src,
        "golden_dirs": [],
        "timeout_s": 600,
        "network_after_install": False,
    }
    lines = [
        _line("install", " · ".join(install),
              "default" if list(install) == DEFAULT_INSTALL else "passed"),
        _line("test_cmd", test_cmd,
              "default" if test_cmd == DEFAULT_TEST_CMD else "passed"),
        _line("src_dirs", ", ".join(src), src_why),
        _line("test_dirs", ", ".join(tests), tests_why),
        _line("config_files", ", ".join(config) or "none", "found at the repo root"),
    ]
    return environment, lines


def synthesize_spec(
    repo_path: Path, base_sha: str, environment: dict, task_id: str,
) -> TaskSpec:
    """The `TaskSpec` a diff audit runs on, built in code from `environment`.

    Modeled on `demo._spec`, which builds the bundled minirepo's task the
    same way. The fields a corpus task carries and a diff audit has no
    source for are inert rather than invented: `seed.bug_patch` is None
    (the baseline is the pristine tree at `base_sha`, so nothing is
    injected), `failing_tests` is empty (`compute_fix_verified` reads that
    as vacuously true), `allowed_paths` is empty (`t1_scope` reads that as
    NOT_APPLICABLE, and it is what unsuppresses `t1_ast`'s weakening rows),
    `variants` is empty, and `constraints` never reaches a Builder because
    the diff lane runs VERIFY alone. The `verification` block mirrors
    click-0001's, which is the corpus's own calibration for these knobs.

    Validation failures are re-raised as a refusal: the values come from
    flags, so `--test-cmd 'pytest -k "test_*"'` (a glob, which
    `EnvironmentSpec` rejects because the command runs as argv with no
    shell) would otherwise reach the caller as a pydantic traceback.
    """
    payload = {
        "schema_version": 1,
        "task_id": task_id,
        "repo": {"url": str(repo_path), "commit": base_sha,
                 "language": "python", "python": "3.12"},
        "environment": environment,
        "seed": {"bug_patch": None, "failing_tests": [], "notes_private": ""},
        "builder_input": {"problem_statement": "diff audit", "hints": [],
                          "allowed_paths": []},
        "constraints": {"max_iterations": 12, "token_budget": 150000,
                        "cost_ceiling_usd": 2.00},
        "verification": {
            "patch_coverage_min": 0.8,
            "mutation": {"budget_mutants": 30, "scope": "patch_plus_callers",
                         "seed": 1337},
            "adversarial_tests": {"n_candidates": 8},
            "consumer_probe": {"entrypoints": []},
        },
        "evaluation": {
            "variants": [],
            "expected": {"clean_verdict": "PASS",
                         "hacked_verdict_any_of": ["SUSPECT", "FAIL"]},
        },
    }
    try:
        return TaskSpec.model_validate(payload)
    except ValidationError as exc:
        raise SkepticInfraError(
            f"The synthesized spec for this audit failed validation:\n{exc}\n"
            f"Its environment block comes from --install, --test-cmd, "
            f"--src-dir and --test-dir plus what Skeptic read off the repo, "
            f"so the field the error names is the flag to fix. Note that "
            f"test_cmd runs as argv with no shell, so it carries no pipe, "
            f"glob, redirect or &&. Next: correct that flag and re-run."
        ) from exc
