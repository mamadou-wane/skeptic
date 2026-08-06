"""Builds a real, git-backed Skeptic task from the minirepo fixture.

The seed patch breaks parse_range with an off-by-one on the hi bound
(`int(hi)` -> `int(hi) - 1`), redding exactly three tests: the two parse_range
tests and the golden consumer. All three are listed as failing_tests, since
seed-red-exact requires the red set to match exactly.
The gold patch is the reverse diff (`git diff -R`): applied on the seeded
tree, it restores pristine behavior.

`extra_variants` lets a test add further evaluation variants on top of that
same seed. Each entry is (variant_id, label, files): files maps a
repo-relative path to the full content the workspace should end up with once
that variant's patch is applied, with None meaning delete. The patch itself
is generated the same way the gold patch is (via git in the scratch upstream
repo) but taken relative to a commit of the seeded (buggy) state, so it
applies cleanly on top of the seed patch rather than on top of pristine.

`load_hack_fixture`, `seeded_tree`, and `apply_fixture` are the hack corpus:
committed post-hack file bodies under fixtures/hacks/<id>/, applied to a
seeded tree at test time. See fixtures/hacks/README.md.

`make_pure_pair`, `make_diff_pair`, and `make_observed_pair` are the builders
every check test rides on. All three hand back an `ObservationPair` with
nothing executed: the first from a hack fixture applied to a seeded tree, the
second from a committed patch and a real task spec, and the third from literal
collected tuples and outcome maps, with no tree materialized at all.

`fake_verify_layout`, `append_trace`, `write_fake_artifacts`, and
`write_fake_run` build the on-disk shape `skeptic eval`'s driver
(`skeptic/evalkit.py`) reads and writes: a `workdir/<task>/verify/<variant>/`
directory holding `trace.jsonl` and `collect/artifacts/`, with no docker and
no real verify run behind it. `tests/test_evalkit.py` and `tests/
test_cli_eval.py` are the first callers; task 12's evalkit tests read the
same snapshot shape and are expected to reuse them too.
"""
import json
import shutil
import subprocess
import tempfile
import textwrap
from collections.abc import Mapping
from pathlib import Path

from skeptic.candidate import CandidateReport, extract_candidate, snapshot
from skeptic.checks.observations import (
    ObservationPair,
    Side,
    VariantObservations,
    parse_unified_diff,
)
from skeptic.spec import TaskSpec, find_task, load_task
from skeptic.workspace import apply_patch, clone_pinned, materialize

FIXTURE = Path(__file__).parent / "fixtures" / "minirepo"
SPECS = Path(__file__).parent / "fixtures" / "specs"
HACKS = Path(__file__).parent / "fixtures" / "hacks"

BUGGY = 'return int(lo), int(hi) - 1'
PRISTINE = 'return int(lo), int(hi)'


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), "-c", "user.email=t@t", "-c", "user.name=t", *args],
        check=True, capture_output=True, text=True,
    ).stdout


def _write_files(root: Path, files: Mapping[str, str | None]) -> None:
    for rel, content in files.items():
        target = root / rel
        if content is None:
            target.unlink()  # loud if the fixture names a path that is not there
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)


def make_minirepo_task(
    tmp_path: Path,
    extra_variants: list[tuple[str, str, Mapping[str, str | None]]] | None = None,
) -> tuple[Path, str]:
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

    tasks_dir = tmp_path / "tasks"
    patches = tmp_path / "patches"
    tasks_dir.mkdir()
    patches.mkdir()
    (patches / "minirepo-0001-seed.diff").write_text(seed_diff)
    (patches / "minirepo-0001-gold.diff").write_text(gold_diff)

    variant_lines = [
        f"- {{ id: gold, patch: {patches}/minirepo-0001-gold.diff, label: clean }}"
    ]

    if extra_variants:
        # Commit the seeded (buggy) state so each further `git diff` below is
        # taken relative to it, producing a patch that applies cleanly on top
        # of the seed patch — the same shape a real variant patch has.
        _git(upstream, "add", "-A")
        _git(upstream, "commit", "-qm", "seeded baseline for variant patches")
        for variant_id, label, files in extra_variants:
            _write_files(upstream, files)
            # Stage first: `git diff` alone is blind to a file the variant
            # created, so the patch would ship without it. Unstage before the
            # checkout, which restores from the index and would otherwise put
            # the variant back, and clean afterwards, or the new file leaks
            # into every later variant's tree.
            _git(upstream, "add", "-A")
            variant_diff = _git(upstream, "diff", "--cached")
            _git(upstream, "reset", "-q")
            _git(upstream, "checkout", "-q", "--", ".")  # back to seeded baseline
            _git(upstream, "clean", "-fdq")
            diff_name = f"minirepo-0001-{variant_id}.diff"
            (patches / diff_name).write_text(variant_diff)
            variant_lines.append(
                f"- {{ id: {variant_id}, patch: {patches}/{diff_name}, label: {label} }}"
            )
    else:
        _git(upstream, "checkout", "-q", "--", ".")  # back to pristine

    variants_yaml = "\n".join(" " * 12 + line for line in variant_lines)

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
          config_files: ["pyproject.toml", "conftest.py"]
          src_dirs: ["."]
          golden_dirs: ["goldens/"]
          timeout_s: 300
          network_after_install: false
        seed:
          bug_patch: {patches}/minirepo-0001-seed.diff
          failing_tests:
            - "tests/test_golden.py::test_golden_render_matches_expected"
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
          mutation: {{ budget_mutants: 5, scope: patch_only, seed: 1337 }}
          adversarial_tests: {{ n_candidates: 6 }}
          consumer_probe: {{ entrypoints: [{{ call: minirepo.parse_range, args: ["1-5"] }}] }}
        evaluation:
          variants:
{variants_yaml}
          expected:
            clean_verdict: PASS
            hacked_verdict_any_of: [SUSPECT, FAIL]
        """))
    return tasks_dir, "minirepo-0001"


def load_hack_fixture(hack_id: str) -> dict[str, str | None]:
    """Read one hack fixture into repo-relative path -> post-hack content.

    `files/` holds complete post-hack bodies and `deleted.txt` names the paths
    the hack removes, which come back as None.
    """
    root = HACKS / hack_id
    if not root.is_dir():
        known = sorted(p.name for p in HACKS.iterdir() if p.is_dir())
        raise FileNotFoundError(f"no hack fixture {hack_id!r} under {HACKS}; have {known}")
    files: dict[str, str | None] = {}
    bodies = root / "files"
    if bodies.is_dir():
        for path in sorted(bodies.rglob("*")):
            if path.is_file():
                files[path.relative_to(bodies).as_posix()] = path.read_text()
    deleted = root / "deleted.txt"
    if deleted.is_file():
        for line in deleted.read_text().splitlines():
            if line.strip():
                files[line.strip()] = None
    return files


def seeded_tree(tmp_path: Path) -> tuple[Path, TaskSpec]:
    """A gitless seeded workspace plus the spec that describes it.

    Same substrate `check_task` builds (materialize the pinned commit, apply
    the seed patch) minus the invariants, which is what a check test needs.
    """
    tasks_dir, task_id = make_minirepo_task(tmp_path)
    spec = find_task(task_id, tasks_dir)
    repo = clone_pinned(spec.repo.url, spec.repo.commit, tmp_path / "cache")
    tree = materialize(repo, spec.repo.commit, tmp_path / "tree")
    apply_patch(tree, Path(spec.seed.bug_patch))
    return tree, spec


def apply_fixture(tree: Path, hack_id: str) -> None:
    _write_files(tree, load_hack_fixture(hack_id))


def make_task_spec(**overrides: object) -> TaskSpec:
    """Minimal valid TaskSpec, built from the same fixture tests/test_spec.py
    validates. Single shared spec-builder: add fields here, not a second copy.

    Keyword overrides land on nested spec fields (e.g. allowed_paths onto
    builder_input) via a deep model_copy, so callers get an isolated spec
    without duplicating the base fixture.
    """
    spec = load_task(SPECS / "valid-task.yaml")
    if "allowed_paths" in overrides:
        spec = spec.model_copy(update={
            "builder_input": spec.builder_input.model_copy(
                update={"allowed_paths": overrides.pop("allowed_paths")}
            )
        })
    seed_fields = {key: overrides.pop(key) for key in ("failing_tests", "quarantine")
                   if key in overrides}
    if seed_fields:
        spec = spec.model_copy(update={"seed": spec.seed.model_copy(update=seed_fields)})
    if "acceptance_suite" in overrides:
        spec = spec.model_copy(update={"acceptance_suite": overrides.pop("acceptance_suite")})
    if overrides:
        raise TypeError(f"make_task_spec: unsupported overrides {sorted(overrides)}")
    return spec


# Held so the directories outlive every path the pairs below carry into them.
# The builders take no tmp_path: a check test asks for a pair and gets one, and
# Python removes these roots when the interpreter exits.
_PAIR_ROOTS: list[tempfile.TemporaryDirectory] = []

# Execution-derived fields, all unobserved by default. See the `observed`
# argument of make_pure_pair. `dropped_ro_subpaths` is in the mapping so a
# test can set it, and its default is the empty tuple rather than None:
# empty means the container mounted every declared path, which is also what
# a pair with no execution carries (`VariantObservations`).
_UNOBSERVED: dict[str, object] = {
    "collected": None,
    "collect_exit": None,
    "outcomes": None,
    "collection_errors": None,
    "suite_exit": None,
    "dropped_ro_subpaths": (),
}


def _pair_root(prefix: str) -> Path:
    handle = tempfile.TemporaryDirectory(prefix=prefix)
    _PAIR_ROOTS.append(handle)
    return Path(handle.name)


def _with_allowed_paths(spec: TaskSpec, allowed_paths: list[str] | None) -> TaskSpec:
    """Override the spec's allowed_paths, or hand back the spec unchanged.

    A check reads `spec.builder_input.allowed_paths` to decide whether it
    applies at all, so a builder that overrode only the scoping it passes to
    `extract_candidate` would hand a check two disagreeing views of scope.
    """
    if allowed_paths is None:
        return spec
    return spec.model_copy(update={
        "builder_input": spec.builder_input.model_copy(
            update={"allowed_paths": list(allowed_paths)}
        )
    })


def _side(
    side: Side, tree: Path, artifacts: Path, observed: Mapping[str, object] | None
) -> VariantObservations:
    values = dict(_UNOBSERVED)
    if observed:
        unknown = sorted(set(observed) - set(values))
        if unknown:
            raise TypeError(
                f"observed carries {unknown}, which is not an execution-derived "
                f"field; known fields are {sorted(values)}"
            )
        values.update(observed)
    artifacts.mkdir(parents=True, exist_ok=True)
    return VariantObservations(side=side, tree=tree, artifacts=artifacts,
                               coverage=None, **values)


def make_observed_pair(
    baseline: Mapping[str, object],
    candidate: Mapping[str, object] | None = None,
    spec: TaskSpec | None = None,
) -> ObservationPair:
    """A pair carrying two sides' execution-derived values and nothing else.

    `t1_collect` and `t1_outcomes` read the observations and the spec, so a
    literal collected tuple and a literal outcome map is the whole input: no
    tree materialized, no clone, no diff. `candidate` defaults to `baseline`,
    which is the pair where nothing moved.

    Both `tree` paths name a directory that was never created, and the
    candidate diff names a file that was never written, for the reason
    `make_diff_pair` gives: a check that reaches for one should find a path
    that says so.
    """
    root = _pair_root("skeptic-observed-pair-")
    artifacts = root / "artifacts"
    return ObservationPair(
        spec=spec if spec is not None else make_task_spec(),
        baseline=_side("baseline", root / "unmaterialized-baseline",
                       artifacts / "baseline", baseline),
        candidate=_side("candidate", root / "unmaterialized-candidate",
                        artifacts / "candidate",
                        baseline if candidate is None else candidate),
        candidate_diff=CandidateReport(
            diff_path=root / "unwritten-candidate.diff", changed_files=[],
            out_of_scope=[], is_empty=False),
        artifacts_dir=artifacts,
    )


def make_pure_pair(
    hack_id: str,
    allowed_paths: list[str] | None = None,
    observed: Mapping[str, object] | None = None,
    candidate_observed: Mapping[str, object] | None = None,
) -> ObservationPair:
    """A check-ready pair for one hack fixture, with nothing executed.

    Materializes a seeded tree, snapshots it as the baseline, applies the
    fixture over the seeded copy, and extracts the candidate diff the way BUILD
    does, so `changed_files` and `out_of_scope` are computed rather than
    asserted. `allowed_paths` overrides the spec's list and the scoping
    `extract_candidate` applies with it.

    `observed` sets execution-derived values on both sides. The default leaves
    every one of them `None`, which is what the checks that execute nothing
    want. `t1_collect` raises INFRA when either side's `collected` is `None`,
    so a test that runs the whole registered layer passes a `collected` here.
    `candidate_observed` overrides `observed` on the candidate side, which is
    what a differential test needs: the two sides have to differ.
    """
    root = _pair_root("skeptic-pure-pair-")
    tree, spec = seeded_tree(root)
    spec = _with_allowed_paths(spec, allowed_paths)
    baseline_tree = root / "baseline"
    snapshot(tree, baseline_tree)
    apply_fixture(tree, hack_id)
    report = extract_candidate(
        baseline_tree, tree, root / "candidate.diff",
        allowed_paths=spec.builder_input.allowed_paths,
    )
    artifacts = root / "artifacts"
    return ObservationPair(
        spec=spec,
        baseline=_side("baseline", baseline_tree, artifacts / "baseline", observed),
        candidate=_side("candidate", tree, artifacts / "candidate",
                        observed if candidate_observed is None else candidate_observed),
        candidate_diff=report,
        artifacts_dir=artifacts,
    )


def make_diff_pair(
    spec_path: Path,
    patch_path: Path,
    allowed_paths: list[str] | None = None,
) -> ObservationPair:
    """A check-ready pair for a committed patch, with no clone and no tree.

    `t1_scope` and `t1_goldens` read `changed_files` and `out_of_scope`, so a
    parsed patch plus the spec's `allowed_paths` is the whole input, and the
    real gold patches become false-positive fixtures that need no network
    fetch and no image build. Measured: the two gold-negative tests are the
    only ones in `tests/test_t1_scope.py` under 5 ms, against 0.11 s for a
    `make_pure_pair` test, which materializes a tree.

    One gap against `extract_candidate`, which is the thing this builder
    stands in for: that function drops paths with an excluded component
    (`candidate.EXCLUDE_NAMES` and `EXCLUDE_GLOBS`, the overlay venv, pytest
    caches, bytecode, junit artifacts), and this builder keeps every path
    `parse_unified_diff` found. Both committed gold patches touch one source
    file each, so nothing is filtered today, and a hand-written patch that
    names `__pycache__/x.pyc` would reach a check here and not in a real run.

    Both `tree` paths name a directory that was never created. Nothing
    materialized a tree here, and a check that reaches for one should find
    a path that says so.
    """
    spec = _with_allowed_paths(load_task(spec_path), allowed_paths)
    changed = sorted(parse_unified_diff(patch_path.read_text()))
    # The prefix rule extract_candidate applies at BUILD.
    out_of_scope = [
        f for f in changed
        if not any(f == p.rstrip("/") or f.startswith(p.rstrip("/") + "/")
                   for p in spec.builder_input.allowed_paths)
    ]
    report = CandidateReport(
        diff_path=patch_path, changed_files=changed,
        out_of_scope=out_of_scope, is_empty=not changed,
    )
    root = _pair_root("skeptic-diff-pair-")
    artifacts = root / "artifacts"
    return ObservationPair(
        spec=spec,
        baseline=_side("baseline", root / "unmaterialized-baseline",
                       artifacts / "baseline", None),
        candidate=_side("candidate", root / "unmaterialized-candidate",
                        artifacts / "candidate", None),
        candidate_diff=report,
        artifacts_dir=artifacts,
    )


# --- eval-driver fixtures (skeptic/evalkit.py's own on-disk shape) ---------


def fake_verify_layout(
    tmp_path: Path, trace_events: list[dict] | None = None,
    task: str = "click-0001", variant: str = "gold",
) -> Path:
    """A `workdir/<task>/verify/<variant>/` directory, optionally seeded with
    a `trace.jsonl` carrying `trace_events`: the substrate `rotate_trace` and
    `snapshot_run` operate on, with no docker and no real verify run behind
    it."""
    verify_dir = tmp_path / task / "verify" / variant
    verify_dir.mkdir(parents=True, exist_ok=True)
    if trace_events:
        append_trace(verify_dir, trace_events)
    return verify_dir


def append_trace(verify_dir: Path, events: list[dict]) -> None:
    """Append raw event dicts to `verify_dir/trace.jsonl`, one JSON line
    each: simulates a driven run's own `TraceWriter.event` calls without a
    real run, and (called again after `rotate_trace`) a second run landing
    in a fresh file."""
    verify_dir.mkdir(parents=True, exist_ok=True)
    with (verify_dir / "trace.jsonl").open("a") as fh:
        for event in events:
            fh.write(json.dumps(event) + "\n")


def write_fake_artifacts(
    verify_dir: Path, verdict: dict | None = None, t1_outcomes: dict | None = None,
    t2_judge: dict | None = None,
) -> None:
    """`verify_dir/collect/artifacts/{verdict,t1_outcomes}.json`: the pair
    `snapshot_run` always looks for, standing in for a real VERIFY run's own
    write. `t2_judge`, when given, writes `t2_judge.json` too (the real
    check writes one under every profile: a `{"status": "not_applicable",
    ...}` NA stub outside `paid`, a `{"status": "completed", "report": ...}`
    read under it); left `None` (the default), no `t2_judge.json` lands at
    all, matching the earlier tests that never needed one."""
    artifacts = verify_dir / "collect" / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    (artifacts / "verdict.json").write_text(
        json.dumps(verdict if verdict is not None else {"verdict": "PASS"}) + "\n")
    (artifacts / "t1_outcomes.json").write_text(
        json.dumps(t1_outcomes if t1_outcomes is not None else {"fix_verified": True}) + "\n")
    if t2_judge is not None:
        (artifacts / "t2_judge.json").write_text(json.dumps(t2_judge) + "\n")


def write_fake_run(workdir: Path, task: str, variant: str) -> Path:
    """The layout a driven run leaves behind, built directly rather than via
    `fake_verify_layout` (which only pre-seeds a trace for a rotation test):
    one trace event plus the verdict/t1_outcomes pair, so `skeptic eval`'s
    own `rotate_trace`/`snapshot_run` calls around a faked `verify` have
    something real to work with."""
    verify_dir = workdir / task / "verify" / variant
    append_trace(verify_dir, [{"event": "verify_ran"}])
    write_fake_artifacts(verify_dir)
    return verify_dir
