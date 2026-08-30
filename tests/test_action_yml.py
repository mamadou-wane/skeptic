"""`action.yml` parses as yaml and carries the load-bearing strings task 4's
brief, and its fix-round-1 findings, call for: no second pinned install ref,
`fail-on`'s three choices validated early, a merge-base failure message that
names `fetch-depth: 0`, no expression spliced directly into a `run:` body,
`--binary` on the diff, stderr captured, and the workdir/patch kept under
`$RUNNER_TEMP`. Static checks only: no live GitHub Actions run here."""
from pathlib import Path

import yaml

ACTION_PATH = Path(__file__).resolve().parent.parent / "action.yml"
ACTION_TEXT = ACTION_PATH.read_text()
ACTION_DATA = yaml.safe_load(ACTION_TEXT)
STEPS = ACTION_DATA["runs"]["steps"]


def test_action_yml_parses_as_a_composite_action():
    assert ACTION_DATA["runs"]["using"] == "composite"
    assert isinstance(STEPS, list) and STEPS


def test_action_yml_declares_base_ref_and_fail_on_with_never_default():
    inputs = ACTION_DATA["inputs"]
    assert set(inputs) == {"base-ref", "fail-on", "install"}
    assert inputs["fail-on"]["default"] == "never"
    assert inputs["base-ref"]["default"] == "${{ github.base_ref }}"


def test_action_yml_passes_the_install_input_through_as_a_flag():
    """A repo whose pytest addopts names a plugin from an extra
    (`hkhonming/lp-to-jira#16`: `--cov`, pytest-cov in `[test]`) needs the
    install line to name that extra. The default is the corpus convention,
    and the value reaches the CLI through the environment, never spliced."""
    inputs = ACTION_DATA["inputs"]
    assert inputs["install"]["default"] == "pip install -q -e . pytest"
    assert 'INSTALL: "${{ inputs.install }}"' in ACTION_TEXT
    assert '--install "$INSTALL"' in ACTION_TEXT


def test_action_yml_installs_from_the_action_path_with_no_second_pinned_ref():
    # The consumer's `uses: ...@ref` is the only version authority: no
    # second git ref (a tag, sha, or branch) pinned inside the action itself.
    assert 'pip install -q "$GITHUB_ACTION_PATH"' in ACTION_TEXT
    assert "git+http" not in ACTION_TEXT
    assert "@refs/" not in ACTION_TEXT


def test_action_yml_states_the_fail_on_choices():
    assert "fail, suspect, never" in ACTION_TEXT


def test_action_yml_merge_base_failure_names_fetch_depth():
    assert "merge-base" in ACTION_TEXT
    assert "fetch-depth: 0" in ACTION_TEXT
    # the two live in the same failure message, not just anywhere in the file
    error_line = next(
        line for line in ACTION_TEXT.splitlines() if "::error::" in line and "merge-base" in line
    )
    assert "fetch-depth: 0" in error_line


def test_action_yml_maps_exit_codes_through_fail_on():
    # never: always 0 · suspect: red on exit >= 1 · fail: red on exit 2 or 3
    assert "never) exit 0 ;;" in ACTION_TEXT
    assert '[ "$CODE" -ge 1 ]' in ACTION_TEXT
    assert '"$CODE" = "2"' in ACTION_TEXT and '"$CODE" = "3"' in ACTION_TEXT


def test_action_yml_runs_skeptic_verify_diff_with_repo_and_base():
    assert "skeptic verify --diff" in ACTION_TEXT
    assert "--repo ." in ACTION_TEXT
    assert "--base " in ACTION_TEXT


def test_action_yml_run_steps_all_declare_a_shell():
    # composite action run steps need an explicit shell; there is no default.
    for step in STEPS:
        if "run" in step:
            assert step.get("shell") == "bash", step


def test_action_yml_never_splices_an_expression_directly_into_a_run_body():
    """Finding r1#2: `${{ inputs.* }}` and `${{ steps.*.outputs.* }}` must
    reach a script through `env:`, read back as `$VAR`, never interpolated
    straight into a `run:` body where a hostile value could inject shell
    syntax. `${{` is only allowed in `default:`/`env:` mappings."""
    for step in STEPS:
        if "run" in step:
            assert "${{" not in step["run"], step.get("name")


def test_action_yml_diff_captures_binary_files():
    assert "git diff --binary" in ACTION_TEXT


def test_action_yml_verify_step_captures_stderr_into_the_summary():
    assert "2>&1" in ACTION_TEXT


def test_action_yml_keeps_patch_and_workdir_under_runner_temp():
    assert 'patch="$RUNNER_TEMP/skeptic-diff.patch"' in ACTION_TEXT
    assert 'out="$RUNNER_TEMP/skeptic-verify.txt"' in ACTION_TEXT
    assert '--workdir "$RUNNER_TEMP/skeptic"' in ACTION_TEXT


def test_action_yml_guards_an_empty_base_ref():
    assert "github.base_ref is only set on pull_request events" in ACTION_TEXT


def test_action_yml_merge_base_calls_do_not_discard_stderr():
    for line in ACTION_TEXT.splitlines():
        if "git merge-base" in line:
            assert "2>/dev/null" not in line


def test_action_yml_validates_fail_on_before_resolving_the_merge_base():
    names = [s.get("name") for s in STEPS]
    validate_idx = names.index("Validate fail-on")
    mergebase_idx = next(i for i, s in enumerate(STEPS) if s.get("id") == "mergebase")
    assert validate_idx < mergebase_idx, names
