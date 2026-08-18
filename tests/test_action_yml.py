"""`action.yml` parses as yaml and carries the load-bearing strings task 4's
brief calls for: no second pinned install ref, `fail-on`'s three choices,
and a merge-base failure message that names `fetch-depth: 0`. Static checks
only: no live GitHub Actions run here."""
from pathlib import Path

import yaml

ACTION_PATH = Path(__file__).resolve().parent.parent / "action.yml"
ACTION_TEXT = ACTION_PATH.read_text()
ACTION_DATA = yaml.safe_load(ACTION_TEXT)


def test_action_yml_parses_as_a_composite_action():
    assert ACTION_DATA["runs"]["using"] == "composite"
    assert isinstance(ACTION_DATA["runs"]["steps"], list) and ACTION_DATA["runs"]["steps"]


def test_action_yml_declares_base_ref_and_fail_on_with_never_default():
    inputs = ACTION_DATA["inputs"]
    assert set(inputs) == {"base-ref", "fail-on"}
    assert inputs["fail-on"]["default"] == "never"
    assert inputs["base-ref"]["default"] == "${{ github.base_ref }}"


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
    assert '[ "$code" -ge 1 ]' in ACTION_TEXT
    assert '"$code" = "2"' in ACTION_TEXT and '"$code" = "3"' in ACTION_TEXT


def test_action_yml_runs_skeptic_verify_diff_with_repo_and_base():
    assert "skeptic verify --diff" in ACTION_TEXT
    assert "--repo ." in ACTION_TEXT
    assert "--base " in ACTION_TEXT


def test_action_yml_run_steps_all_declare_a_shell():
    # composite action run steps need an explicit shell; there is no default.
    for step in ACTION_DATA["runs"]["steps"]:
        if "run" in step:
            assert step.get("shell") == "bash", step
