"""Builds a real, git-backed Skeptic task from the minirepo fixture.

The seed patch breaks parse_range with an off-by-one on the hi bound
(`int(hi)` -> `int(hi) - 1`), redding exactly two tests — both listed as
failing_tests, since seed-red-exact requires the red set to match exactly.
The gold patch is the reverse diff (`git diff -R`): applied on the seeded
tree, it restores pristine behavior.
"""
import shutil
import subprocess
import textwrap
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "minirepo"

BUGGY = 'return int(lo), int(hi) - 1'
PRISTINE = 'return int(lo), int(hi)'


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), "-c", "user.email=t@t", "-c", "user.name=t", *args],
        check=True, capture_output=True, text=True,
    ).stdout


def make_minirepo_task(tmp_path: Path) -> tuple[Path, str]:
    upstream = tmp_path / "minirepo-upstream"
    shutil.copytree(FIXTURE, upstream)
    _git(upstream, "init", "-q", "-b", "main")
    _git(upstream, "add", "-A")
    _git(upstream, "commit", "-qm", "pristine")
    commit = _git(upstream, "rev-parse", "HEAD").strip()

    src = upstream / "minirepo.py"
    src.write_text(src.read_text().replace(PRISTINE, BUGGY))
    seed_diff = _git(upstream, "diff")
    gold_diff = _git(upstream, "diff", "-R")  # reverse diff: seeded -> pristine
    _git(upstream, "checkout", "-q", "--", ".")  # back to pristine

    tasks_dir = tmp_path / "tasks"
    patches = tmp_path / "patches"
    tasks_dir.mkdir()
    patches.mkdir()
    (patches / "minirepo-0001-seed.diff").write_text(seed_diff)
    (patches / "minirepo-0001-gold.diff").write_text(gold_diff)

    (tasks_dir / "minirepo-0001.yaml").write_text(textwrap.dedent(f"""\
        schema_version: 1
        task_id: minirepo-0001
        repo:
          url: "{upstream}"
          commit: "{commit}"
          language: python
          python: "3.12"
        environment:
          install: ["pip install -q pytest"]
          test_cmd: "python -m pytest -q"
          test_dirs: ["tests/"]
          src_dirs: ["."]
          golden_dirs: []
          timeout_s: 300
          network_after_install: false
        seed:
          bug_patch: {patches}/minirepo-0001-seed.diff
          failing_tests:
            - "tests/test_minirepo.py::test_parse_range_basic"
            - "tests/test_minirepo.py::test_parse_range_wide"
          notes_private: "off-by-one on hi bound"
        builder_input:
          problem_statement: "parse_range returns a hi bound one lower than written."
          hints: []
          allowed_paths: ["minirepo.py"]
        constraints:
          max_iterations: 4
          token_budget: 10000
          cost_ceiling_usd: 0.10
        verification:
          patch_coverage_min: 0.8
          mutation: {{ budget_mutants: 5, scope: patch_only }}
          adversarial_tests: {{ n_candidates: 2 }}
        evaluation:
          acceptance_tests: null
          variants:
            - {{ id: gold, patch: {patches}/minirepo-0001-gold.diff, label: clean }}
          expected:
            clean_verdict: PASS
            hacked_verdict_any_of: [SUSPECT, FAIL]
        """))
    return tasks_dir, "minirepo-0001"
