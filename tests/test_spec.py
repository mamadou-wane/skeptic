# tests/test_spec.py
from pathlib import Path

import pytest
from pydantic import ValidationError

from skeptic import spec as spec_module
from skeptic.errors import SkepticInfraError
from skeptic.spec import MutationSpec, TaskSpec, find_task, load_task

FIXTURES = Path(__file__).parent / "fixtures" / "specs"


def test_valid_task_loads():
    spec = load_task(FIXTURES / "valid-task.yaml")
    assert isinstance(spec, TaskSpec)
    assert spec.task_id == "click-0001"
    assert spec.repo.commit.startswith("aaaabbbb")
    assert spec.environment.timeout_s == 600
    assert spec.seed.failing_tests == ["tests/test_termui.py::test_progressbar_width"]
    assert spec.evaluation.variants[0].label == "clean"


def test_unknown_field_rejected(tmp_path):
    text = (FIXTURES / "valid-task.yaml").read_text().replace(
        "task_id: click-0001", "task_id: click-0001\nsurprise_field: 1"
    )
    p = tmp_path / "bad.yaml"
    p.write_text(text)
    with pytest.raises(SkepticInfraError, match="surprise_field"):
        load_task(p)


def test_missing_required_field_names_it(tmp_path):
    # `test_cmd` rather than `bug_patch`: the seed patch became optional when
    # `verify --diff` landed (a diff audit has no bug to inject), and this
    # test is about the validator naming whatever is genuinely missing.
    text = (FIXTURES / "valid-task.yaml").read_text().replace(
        '  test_cmd: "python -m pytest -q"\n', "")
    p = tmp_path / "bad.yaml"
    p.write_text(text)
    with pytest.raises(SkepticInfraError, match="test_cmd"):
        load_task(p)


def test_no_clean_variant_rejected(tmp_path):
    text = (FIXTURES / "valid-task.yaml").read_text().replace("label: clean", "label: hacked")
    p = tmp_path / "bad.yaml"
    p.write_text(text)
    with pytest.raises(SkepticInfraError, match="clean"):
        load_task(p)


def test_missing_file_is_infra_error_with_next_command(tmp_path):
    with pytest.raises(
        SkepticInfraError, match=r"`skeptic tasks` to see available task ids"
    ) as excinfo:
        load_task(tmp_path / "nope.yaml")
    assert "tasks list" not in str(excinfo.value)


def test_find_task_by_id(tmp_path):
    dest = tmp_path / "click-0001.yaml"
    dest.write_text((FIXTURES / "valid-task.yaml").read_text())
    spec = find_task("click-0001", tmp_path)
    assert spec.task_id == "click-0001"
    with pytest.raises(SkepticInfraError, match="click-9999"):
        find_task("click-9999", tmp_path)


def test_environment_config_files_defaults_empty():
    spec = load_task(FIXTURES / "valid-task.yaml")
    assert spec.environment.config_files == []


# 2026-07-26 review finding 6: seed --check runs test_cmd through sh -c,
# BUILD runs it through shlex.split + exec_argv (no shell). Reject anything
# where those two disagree at spec load, before BUILD spends money finding
# the divergence with an image already built.


def test_test_cmd_with_shell_metacharacter_rejected(tmp_path):
    text = (FIXTURES / "valid-task.yaml").read_text().replace(
        'test_cmd: "python -m pytest -q"',
        'test_cmd: "python -m pytest -q && rm -rf /"',
    )
    p = tmp_path / "bad.yaml"
    p.write_text(text)
    with pytest.raises(SkepticInfraError, match="shell metacharacter"):
        load_task(p)


def test_test_cmd_with_env_prefix_rejected(tmp_path):
    text = (FIXTURES / "valid-task.yaml").read_text().replace(
        'test_cmd: "python -m pytest -q"',
        'test_cmd: "COLUMNS=80 python -m pytest -q"',
    )
    p = tmp_path / "bad.yaml"
    p.write_text(text)
    with pytest.raises(SkepticInfraError, match="environment-variable"):
        load_task(p)


def test_plain_test_cmd_still_loads():
    spec = load_task(FIXTURES / "valid-task.yaml")
    assert spec.environment.test_cmd == "python -m pytest -q"


# 2026-07-26 review finding 4: shlex.split and sh -c tokenize a quoted
# argument identically. Banning `"` and `'` would remove the only way to
# express an argument containing a space, since test_cmd is one string.


def test_quoted_test_cmd_loads_and_round_trips_to_expected_argv(tmp_path):
    import shlex

    text = (FIXTURES / "valid-task.yaml").read_text().replace(
        'test_cmd: "python -m pytest -q"',
        "test_cmd: 'python -m pytest -q -k \"not slow\"'",
    )
    p = tmp_path / "quoted.yaml"
    p.write_text(text)
    spec = load_task(p)
    assert spec.environment.test_cmd == 'python -m pytest -q -k "not slow"'
    assert shlex.split(spec.environment.test_cmd) == [
        "python", "-m", "pytest", "-q", "-k", "not slow",
    ]


def test_unbalanced_quote_in_test_cmd_rejected_at_load(tmp_path):
    text = (FIXTURES / "valid-task.yaml").read_text().replace(
        'test_cmd: "python -m pytest -q"',
        "test_cmd: 'python -m pytest -q -k \"not slow'",
    )
    p = tmp_path / "bad.yaml"
    p.write_text(text)
    with pytest.raises(SkepticInfraError, match="unbalanced quoting"):
        load_task(p)


# `seed.quarantine` is M5 surface landed early (Task 11). Part 3 defines it as
# known-flaky test ids excluded from evidence and `SeedSpec` forbids extra
# fields, so a task file could not carry the key at all until it existed.
# No M3 fixture is flaky and neither corpus task ships it.


def test_seed_quarantine_defaults_empty():
    spec = load_task(FIXTURES / "valid-task.yaml")
    assert spec.seed.quarantine == []


def test_seed_quarantine_accepts_nodeids(tmp_path):
    text = (FIXTURES / "valid-task.yaml").read_text().replace(
        '  notes_private: "off-by-one in width calc"',
        "  quarantine:\n"
        '    - "tests/test_termui.py::test_flaky_pager"\n'
        '    - "tests/test_utils.py::test_timing_sensitive"\n'
        '  notes_private: "off-by-one in width calc"',
    )
    p = tmp_path / "quarantined.yaml"
    p.write_text(text)
    assert load_task(p).seed.quarantine == [
        "tests/test_termui.py::test_flaky_pager",
        "tests/test_utils.py::test_timing_sensitive",
    ]


# M4 wave A (DECISIONS.md #98): mutation.seed makes a mutation run
# reproducible, and consumer_probe.entrypoints names the callables the probe
# drives. Both are defaulted so schema_version stays 1 and every existing
# task YAML loads unchanged.


def test_spec_defaults_probe_and_seed_when_absent():
    spec = load_task(FIXTURES / "valid-task.yaml")
    assert spec.verification.mutation.seed == 1337
    assert spec.verification.consumer_probe.entrypoints == []


def test_spec_accepts_a_probe_entrypoint_with_args_and_kwargs(tmp_path):
    text = (FIXTURES / "valid-task.yaml").read_text().replace(
        'mutation: { budget_mutants: 30, scope: patch_plus_callers, seed: 1337 }',
        'mutation: { budget_mutants: 30, scope: patch_plus_callers, seed: 1337 }\n'
        '  consumer_probe:\n'
        '    entrypoints:\n'
        '      - { call: "click.utils._make_default_short_help", '
        'args: ["Show the version and exit."], kwargs: { max_length: 45 } }',
    )
    p = tmp_path / "probed.yaml"
    p.write_text(text)
    entrypoints = load_task(p).verification.consumer_probe.entrypoints
    assert len(entrypoints) == 1
    assert entrypoints[0].call == "click.utils._make_default_short_help"
    assert entrypoints[0].args == ["Show the version and exit."]
    assert entrypoints[0].kwargs == {"max_length": 45}


@pytest.mark.parametrize("bad_call", ["os.system('x')", "a", "a..b"])
def test_spec_rejects_a_probe_call_that_is_not_a_dotted_identifier(tmp_path, bad_call):
    text = (FIXTURES / "valid-task.yaml").read_text().replace(
        'mutation: { budget_mutants: 30, scope: patch_plus_callers, seed: 1337 }',
        'mutation: { budget_mutants: 30, scope: patch_plus_callers, seed: 1337 }\n'
        '  consumer_probe:\n'
        '    entrypoints:\n'
        f'      - {{ call: "{bad_call}" }}',
    )
    p = tmp_path / "bad.yaml"
    p.write_text(text)
    with pytest.raises(SkepticInfraError, match="dotted path"):
        load_task(p)


def test_spec_rejects_unknown_probe_keys(tmp_path):
    text = (FIXTURES / "valid-task.yaml").read_text().replace(
        'mutation: { budget_mutants: 30, scope: patch_plus_callers, seed: 1337 }',
        'mutation: { budget_mutants: 30, scope: patch_plus_callers, seed: 1337 }\n'
        '  consumer_probe:\n'
        '    entrypoints:\n'
        '      - { call: "click.utils._make_default_short_help", surprise_field: 1 }',
    )
    p = tmp_path / "bad.yaml"
    p.write_text(text)
    with pytest.raises(SkepticInfraError, match="surprise_field"):
        load_task(p)


def test_spec_rejects_unknown_consumer_probe_keys(tmp_path):
    """`test_spec_rejects_unknown_probe_keys` above pins `ProbeEntrypoint`'s own
    `extra="forbid"`; this pins `ConsumerProbeSpec`'s, an unknown key sitting
    alongside `entrypoints` rather than inside one of its entries."""
    text = (FIXTURES / "valid-task.yaml").read_text().replace(
        'mutation: { budget_mutants: 30, scope: patch_plus_callers, seed: 1337 }',
        'mutation: { budget_mutants: 30, scope: patch_plus_callers, seed: 1337 }\n'
        '  consumer_probe:\n'
        '    entrypoints: []\n'
        '    surprise_field: 1',
    )
    p = tmp_path / "bad.yaml"
    p.write_text(text)
    with pytest.raises(SkepticInfraError, match="surprise_field"):
        load_task(p)


def test_spec_mutation_seed_round_trips_model_dump():
    spec = load_task(FIXTURES / "valid-task.yaml")
    dumped = spec.verification.mutation.model_dump()
    assert dumped["seed"] == 1337
    assert MutationSpec.model_validate(dumped) == spec.verification.mutation


def _task_dict(**overrides):
    # The field values below mirror tasks/click-0001.yaml.
    base = {
        "schema_version": 1, "task_id": "t-0001",
        "repo": {"url": "https://example.invalid/r", "commit": "a" * 40,
                 "language": "python", "python": "3.12"},
        "environment": {"install": ["pip install -q -e . pytest"],
                        "test_cmd": "python -m pytest -q",
                        "test_dirs": ["tests/"], "src_dirs": ["src/pkg/"],
                        "timeout_s": 600},
        "seed": {"bug_patch": "patches/t-0001-seed.diff",
                 "failing_tests": ["tests/t.py::test_a"]},
        "builder_input": {"problem_statement": "x", "allowed_paths": ["src/pkg/"]},
        "constraints": {"max_iterations": 1, "token_budget": 1,
                        "cost_ceiling_usd": 1.0},
        "verification": {"patch_coverage_min": 0.8,
                         "mutation": {"budget_mutants": 1, "scope": "patch_only"},
                         "adversarial_tests": {"n_candidates": 2}},
        "evaluation": {"variants": [
            {"id": "gold", "patch": "patches/t-0001-gold.diff", "label": "clean"},
            {"id": "gold-prime", "patch": "patches/t-0001-gp.diff", "label": "clean"}],
            "expected": {"clean_verdict": "PASS",
                         "hacked_verdict_any_of": ["SUSPECT", "FAIL"]}},
    }
    return {**base, **overrides}


@pytest.mark.parametrize("field", ["test_dirs", "config_files", "golden_dirs"])
@pytest.mark.parametrize(
    "raw",
    ["/etc", "../escape", "a/../b", "C:\\Windows", "\\\\server\\share", "", "."],
)
def test_protected_mount_subpath_syntax_is_rejected(field, raw):
    payload = _task_dict()
    environment = dict(payload["environment"])
    environment[field] = [raw]
    payload["environment"] = environment

    with pytest.raises(ValidationError, match=field):
        TaskSpec.model_validate(payload)


def test_protected_mount_subpath_normalizes_for_use_without_rewriting_spec():
    payload = _task_dict()
    environment = dict(payload["environment"])
    environment["test_dirs"] = ["./tests//"]
    payload["environment"] = environment

    task = TaskSpec.model_validate(payload)

    assert task.environment.test_dirs == ["./tests//"]
    assert spec_module.normalize_ro_subpath(task.environment.test_dirs[0]) == "tests"


def test_acceptance_suite_block_parses_and_defaults_to_none():
    spec = TaskSpec.model_validate(_task_dict())
    assert spec.acceptance_suite is None
    spec2 = TaskSpec.model_validate(_task_dict(acceptance_suite={
        "path": "acceptance/click-0001/",
        "must_pass_on": ["pristine", "gold", "gold-prime"],
        "must_fail_on": ["seeded"],
    }))
    assert spec2.acceptance_suite.path == "acceptance/click-0001/"
    assert spec2.acceptance_suite.must_fail_on == ["seeded"]


def test_acceptance_suite_rejects_unknown_tree_names():
    with pytest.raises(ValidationError, match="not-a-variant"):
        TaskSpec.model_validate(_task_dict(acceptance_suite={
            "path": "acceptance/x/",
            "must_pass_on": ["pristine", "not-a-variant"],
            "must_fail_on": ["seeded"],
        }))


def test_acceptance_suite_requires_seeded_in_must_fail_on():
    with pytest.raises(ValidationError, match="seeded"):
        TaskSpec.model_validate(_task_dict(acceptance_suite={
            "path": "acceptance/x/",
            "must_pass_on": ["pristine"],
            "must_fail_on": [],
        }))


def test_acceptance_suite_rejects_a_name_in_both_lists():
    # Before this rule, the unknown-name check unions must_pass_on and
    # must_fail_on before checking membership, so a name in both loads
    # cleanly and check_task then materializes that tree twice with one
    # branch guaranteed to fail, reporting a corpus authoring mistake as an
    # invariant failure.
    with pytest.raises(ValidationError, match="both"):
        TaskSpec.model_validate(_task_dict(acceptance_suite={
            "path": "acceptance/x/",
            "must_pass_on": ["pristine", "seeded"],
            "must_fail_on": ["seeded"],
        }))


def test_acceptance_suite_must_fail_on_every_hacked_variant():
    with pytest.raises(ValidationError, match="h5"):
        TaskSpec.model_validate(_task_dict(
            evaluation={"variants": [
                {"id": "gold", "patch": "p/g.diff", "label": "clean"},
                {"id": "h5", "patch": "p/h5.diff", "label": "hacked",
                 "hack_category": "H5"}],
                "expected": {"clean_verdict": "PASS",
                             "hacked_verdict_any_of": ["SUSPECT", "FAIL"]}},
            acceptance_suite={"path": "acceptance/x/",
                              "must_pass_on": ["pristine", "gold"],
                              "must_fail_on": ["seeded"]}))


def test_acceptance_tests_stub_is_gone():
    # the old nested stub must now be rejected by extra="forbid"
    bad = _task_dict()
    bad["evaluation"]["acceptance_tests"] = None
    with pytest.raises(ValidationError):
        TaskSpec.model_validate(bad)


def test_seed_without_a_bug_patch_validates_when_there_are_no_variants():
    """`verify --diff` synthesizes a spec whose baseline is the pristine tree
    at the audited base commit, so there is no bug to inject and no patch to
    name. `git apply` exits 128 on an empty patch, so a placeholder file
    could not have stood in for the absent one."""
    spec = TaskSpec.model_validate(_task_dict(
        seed={"bug_patch": None, "failing_tests": [], "notes_private": ""},
        evaluation={"variants": [],
                    "expected": {"clean_verdict": "PASS",
                                 "hacked_verdict_any_of": ["SUSPECT", "FAIL"]}}))
    assert spec.seed.bug_patch is None
    assert spec.seed.failing_tests == []


def test_seed_without_a_bug_patch_rejected_when_variants_exist():
    """The other direction: a corpus task's variant patches apply on top of
    the seeded tree, and `seed --check` builds that tree from bug_patch, so
    a yaml that omits it has to fail at load. Without this rule the field's
    optionality reaches `Path(None)` inside seedcheck as a bare TypeError."""
    with pytest.raises(ValidationError, match="bug_patch"):
        TaskSpec.model_validate(_task_dict(
            seed={"bug_patch": None, "failing_tests": [], "notes_private": ""}))


def test_every_corpus_task_declares_its_seed_patch():
    """The rule above, held against the real corpus rather than a fixture."""
    from skeptic.spec import list_tasks

    for spec in list_tasks(Path(__file__).parent.parent / "tasks"):
        assert spec.seed.bug_patch, spec.task_id


def test_evaluation_without_variants_validates():
    """A synthesized diff-audit spec declares no variants: there is no corpus
    task behind it, so there is no gold patch to require."""
    spec = TaskSpec.model_validate(_task_dict(evaluation={
        "variants": [],
        "expected": {"clean_verdict": "PASS",
                     "hacked_verdict_any_of": ["SUSPECT", "FAIL"]}}))
    assert spec.evaluation.variants == []


def test_variants_without_a_clean_one_still_rejected():
    """Regression on the escape above: relaxing the empty case must not
    relax a real corpus task that lists only hacks."""
    with pytest.raises(ValidationError, match="clean"):
        TaskSpec.model_validate(_task_dict(evaluation={
            "variants": [{"id": "h1", "patch": "p/h1.diff", "label": "hacked",
                          "hack_category": "H1"}],
            "expected": {"clean_verdict": "PASS",
                         "hacked_verdict_any_of": ["SUSPECT", "FAIL"]}}))


def test_environment_constraints_is_optional_and_loads_a_path(tmp_path):
    """`environment.constraints` names a pip constraints file, relative to the
    checkout like `seed.bug_patch`. Absent, it is None and every install path
    runs unpinned, the pre-M7 behavior the diff lane still relies on."""
    assert load_task(FIXTURES / "valid-task.yaml").environment.constraints is None
    text = (FIXTURES / "valid-task.yaml").read_text().replace(
        "  install:", "  constraints: constraints/click.txt\n  install:", 1)
    p = tmp_path / "pinned.yaml"
    p.write_text(text)
    spec = load_task(p)
    assert spec.environment.constraints == "constraints/click.txt"
    assert spec.environment.constraints_file == Path("constraints/click.txt")
    assert load_task(FIXTURES / "valid-task.yaml").environment.constraints_file is None
