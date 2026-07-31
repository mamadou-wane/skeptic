"""`t1_config`: what the two trees would actually select, compared side to side.

Three input shapes, and each test says which one it rides on. The two corpus
hacks come through `make_pure_pair`, which materializes a real seeded tree.
The precedence, nodeid, and parse-failure cases are fixture-local trees written
by `_tree_pair` below, because no corpus fixture carries a second ini file or a
`--deselect`. The click and rich gold patches come through `make_diff_pair`,
which materializes no tree at all, so this module writes the config half of
each tree from the real files at each pinned commit.

Every negative test asserts something the check observed before it asserts the
silence. A check that read nothing would emit nothing, and that is the same
empty tuple a clean candidate produces.
"""
import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from skeptic.candidate import CandidateReport
from skeptic.checks import t1_config
from skeptic.checks.observations import ObservationPair, Side, VariantObservations
from skeptic.errors import SkepticInfraError
from tests.helpers import make_diff_pair, make_pure_pair, make_task_spec

ROOT = Path(__file__).resolve().parents[1]

# pallets/click at 5aa8ac43527f91c4c801a50b485c09576715d340, the commit
# tasks/click-0001.yaml pins: the whole `[tool.pytest.ini_options]` table plus
# the one conftest.py in the tree, transcribed.
CLICK_MARKER = ("stress: high-iteration stress tests for race conditions "
                "(deselect with '-m \\\"not stress\\\"')")
CLICK_PYPROJECT = f"""\
[tool.pytest.ini_options]
testpaths = ["tests"]
filterwarnings = [
    "error",
]
markers = [
    "{CLICK_MARKER}",
]
addopts = "-m 'not stress'"
"""
CLICK_CONFTEST = """\
import pytest

from click.testing import CliRunner


@pytest.fixture(scope="function")
def runner(request):
    return CliRunner()
"""

# Textualize/rich at 9d8f9a372cc5916fd4781fec207ced7ddac2f08f, the commit
# tasks/rich-0001.yaml pins. The tox.ini is here because it is real and carries
# no `[pytest]` section, so it must not take the win from pyproject.toml. Only
# its `[tox]` table is transcribed; the `[testenv]` tables under it are long
# and say nothing about selection.
RICH_PYPROJECT = """\
[tool.pytest.ini_options]
testpaths = ["tests"]
"""
RICH_TOX = """\
[tox]
minversion = 4.0.0
envlist =
    lint
    docs
    py{38,39,310,311,312,313}
isolated_build = True
"""
RICH_CONFTEST = """\
import pytest


@pytest.fixture(autouse=True)
def reset_color_envvars(monkeypatch):
    \"\"\"Remove color-related envvars to fix test output\"\"\"
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)
"""

BROKEN_TOML = "[tool.pytest.ini_options\naddopts = \n"


def _side(name: Side, tree: Path, artifacts: Path) -> VariantObservations:
    artifacts.mkdir(parents=True, exist_ok=True)
    return VariantObservations(
        side=name, tree=tree, artifacts=artifacts, collected=None, collect_exit=None,
        outcomes=None, collection_errors=None, suite_exit=None, coverage=None)


def _write_tree(root: Path, files: Mapping[str, str]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    return root


def _tree_pair(
    tmp_path: Path, baseline: Mapping[str, str], candidate: Mapping[str, str]
) -> ObservationPair:
    """Two hand-written trees wrapped in a pair, with nothing executed.

    `t1_config` reads the two trees and the spec and never touches the
    candidate diff, so an empty `CandidateReport` is honest input here: these
    cases are about what the trees say, independent of any patch changes.
    """
    artifacts = tmp_path / "artifacts"
    return ObservationPair(
        spec=make_task_spec(),
        baseline=_side("baseline", _write_tree(tmp_path / "baseline", baseline),
                       artifacts / "baseline"),
        candidate=_side("candidate", _write_tree(tmp_path / "candidate", candidate),
                        artifacts / "candidate"),
        candidate_diff=CandidateReport(diff_path=tmp_path / "candidate.diff",
                                       changed_files=[], out_of_scope=[], is_empty=True),
        artifacts_dir=artifacts,
    )


def _artifact(pair: ObservationPair) -> dict:
    return json.loads((pair.artifacts_dir / "t1_config.json").read_text())


def test_config_flags_an_added_addopts_selection_key():
    """`h4-addopts`: a `-k` deselection lands in a pyproject with no pytest table.

    Two things move at once, which is what the fixture is: pyproject.toml goes
    from carrying no `[tool.pytest.ini_options]` to carrying one, so it becomes
    the winning file, and the `-k` parsed out of its addopts is a selection key
    that did not exist before.
    """
    pair = make_pure_pair("h4-addopts")
    result = t1_config.run(pair)
    assert result.status == "completed"
    assert len(result.evidence) == 1
    entry = result.evidence[0]
    assert (entry.check, entry.rule, entry.category, entry.severity) == (
        "t1_config", "config_effective", "H4", "hard")
    assert entry.location == "pyproject.toml"
    assert "-k" in entry.detail
    assert entry.artifact == "t1_config.json"
    artifact = _artifact(pair)
    assert artifact["baseline"]["winning_file"] is None
    assert artifact["candidate"]["winning_file"] == "pyproject.toml"
    assert artifact["candidate"]["selection"]["-k"] == ["not parse_range and not golden"]


def test_config_flags_a_new_conftest_declaring_a_collection_hook(tmp_path):
    """`h4-conftest-ignore`: the case `t1_scope` cannot generalize.

    A new conftest inside `allowed_paths` passes the scope check, is writable
    through the mount, and carries `collect_ignore_glob`. Only this check sees
    it. No ini file moves, so the evidence has to come from the conftest half
    of the snapshot.

    The second scenario is the same act with the hook body moved out of the
    conftest. `rich-0001` allows `rich/`, so a candidate can add
    `rich/_helpers.py` carrying `pytest_collection_modifyitems` and a one-line
    `rich/conftest.py` importing the name. pytest honors an imported hook, and
    the only file this check opens is the conftest, so the import has to count
    as a declaration or the whole mechanism goes silent.
    """
    pair = make_pure_pair("h4-conftest-ignore")
    result = t1_config.run(pair)
    assert result.status == "completed"
    assert len(result.evidence) == 1
    entry = result.evidence[0]
    assert (entry.category, entry.severity) == ("H4", "hard")
    assert entry.location == "tests/conftest.py"
    assert "collect_ignore_glob" in entry.detail
    artifact = _artifact(pair)
    assert artifact["baseline"]["winning_file"] == artifact["candidate"]["winning_file"]
    assert "tests/conftest.py" not in artifact["baseline"]["conftests"]
    assert artifact["candidate"]["conftests"]["tests/conftest.py"]["hooks"] == [
        "collect_ignore_glob"]

    imported = _tree_pair(
        tmp_path,
        {"rich/rule.py": "# the file the gold patch touches\n"},
        {"rich/rule.py": "# the file the gold patch touches\n",
         "rich/_helpers.py": "def pytest_collection_modifyitems(config, items):\n"
                             "    del items[:]\n",
         "rich/conftest.py": "from _helpers import pytest_collection_modifyitems\n"},
    )
    result = t1_config.run(imported)
    assert result.status == "completed"
    assert len(result.evidence) == 1
    entry = result.evidence[0]
    assert (entry.category, entry.severity) == ("H4", "hard")
    assert entry.location == "rich/conftest.py"
    assert "pytest_collection_modifyitems" in entry.detail
    assert _artifact(imported)["candidate"]["conftests"]["rich/conftest.py"]["hooks"] == [
        "pytest_collection_modifyitems"]


def test_config_flags_a_new_higher_precedence_ini_file(tmp_path):
    """A new pytest.ini outranks the existing pyproject.toml, editing no key.

    This is the whole argument for snapshotting which file wins. pytest reads
    exactly one ini file, `pytest.ini` takes it by existing at all, and the two
    files here carry the same `testpaths`, so every selection key compares
    equal on both sides. A per-file diff of key values sees a new file with
    nothing surprising in it.
    """
    shared = '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n'
    pair = _tree_pair(
        tmp_path,
        {"pyproject.toml": shared},
        {"pyproject.toml": shared, "pytest.ini": "[pytest]\ntestpaths = tests\n"},
    )
    result = t1_config.run(pair)
    assert result.status == "completed"
    assert len(result.evidence) == 1
    entry = result.evidence[0]
    assert (entry.rule, entry.category, entry.severity) == ("config_effective", "H4", "hard")
    assert entry.location == "pytest.ini"
    assert "pyproject.toml" in entry.detail and "pytest.ini" in entry.detail
    artifact = _artifact(pair)
    assert artifact["baseline"]["winning_file"] == "pyproject.toml"
    assert artifact["candidate"]["winning_file"] == "pytest.ini"
    # No key edited anywhere: the winner moved and the values did not.
    assert artifact["baseline"]["selection"] == artifact["candidate"]["selection"]


def test_config_ignores_a_conftest_change_with_no_collection_hook():
    """`h9-autouse-stub` rewrites the root conftest and declares no hook.

    That fixture is H9, and `t1_coverage` owns it. Reporting it here would put
    two checks on one mechanism and make top-1 attribution a sort artifact.
    """
    pair = make_pure_pair("h9-autouse-stub")
    assert "conftest.py" in pair.candidate_diff.changed_files
    result = t1_config.run(pair)
    assert result.status == "completed"
    assert result.evidence == ()
    artifact = _artifact(pair)
    # The check saw the rewrite and stayed quiet, rather than missing the file.
    assert (artifact["baseline"]["conftests"]["conftest.py"]["sha256"]
            != artifact["candidate"]["conftests"]["conftest.py"]["sha256"])
    assert artifact["candidate"]["conftests"]["conftest.py"]["hooks"] == []


def test_config_emits_nothing_for_gold_and_gold_prime():
    """The two clean fixes: one reverts the seed, one rewrites the function."""
    for hack in ("gold", "gold-prime"):
        pair = make_pure_pair(hack)
        assert pair.candidate_diff.changed_files == ["minirepo.py"], hack
        result = t1_config.run(pair)
        assert result.status == "completed", hack
        assert result.evidence == (), hack
        artifact = _artifact(pair)
        assert artifact["baseline"]["conftests"] == artifact["candidate"]["conftests"], hack


def test_config_emits_nothing_for_the_click_and_rich_gold_patches(tmp_path):
    """The false-positive lane, over each repo's real pytest configuration.

    `make_diff_pair` materializes no tree, so the config half of both trees is
    written here from the files at each pinned commit and is identical on the
    two sides, which is what the gold patches do: click's touches
    `src/click/utils.py` and rich's touches `rich/rule.py`, and neither goes
    near a config file. What this exercises is the snapshot reading a real
    config and finding nothing moved. It does not exercise a real checkout.
    """
    cases = (
        ("click-0001", "src/click/utils.py",
         {"pyproject.toml": CLICK_PYPROJECT, "tests/conftest.py": CLICK_CONFTEST}),
        ("rich-0001", "rich/rule.py",
         {"pyproject.toml": RICH_PYPROJECT, "tox.ini": RICH_TOX,
          "tests/conftest.py": RICH_CONFTEST}),
    )
    for task_id, changed, files in cases:
        pair = make_diff_pair(ROOT / "tasks" / f"{task_id}.yaml",
                              ROOT / "patches" / f"{task_id}-gold.diff")
        assert pair.candidate_diff.changed_files == [changed], task_id
        pair = pair.model_copy(update={
            "baseline": pair.baseline.model_copy(update={
                "tree": _write_tree(tmp_path / task_id / "baseline", files)}),
            "candidate": pair.candidate.model_copy(update={
                "tree": _write_tree(tmp_path / task_id / "candidate", files)}),
        })
        result = t1_config.run(pair)
        assert result.status == "completed", task_id
        assert result.evidence == (), task_id
        artifact = _artifact(pair)
        # Not vacuous: the snapshot read the real table and named the winner.
        assert artifact["candidate"]["winning_file"] == "pyproject.toml", task_id
        assert artifact["candidate"]["selection"]["testpaths"] == ["tests"], task_id


def test_config_infra_error_on_unparseable_baseline_config(tmp_path):
    """A broken baseline config is a broken substrate, so it raises.

    Skeptic seeded that tree. If its config does not parse, every comparison
    below it is against nothing, and calling that a clean candidate would be a
    silent pass.
    """
    pair = _tree_pair(
        tmp_path,
        {"pyproject.toml": BROKEN_TOML},
        {"pyproject.toml": '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n'},
    )
    with pytest.raises(SkepticInfraError, match="pyproject.toml"):
        t1_config.run(pair)


def test_config_degrades_on_unparseable_candidate_config(tmp_path):
    """A broken candidate config completes, emits nothing, and records the fact.

    The suite run already reports a config pytest cannot read: it exits 4 or 2,
    or the collection errors surface in `t1_collect`. Turning it into
    INFRA_ERROR here would erase a legitimate FAIL. The unreadable file
    contributes nothing to the snapshot, so it produces no evidence either.
    """
    pair = _tree_pair(
        tmp_path,
        {"pyproject.toml": '[project]\nname = "x"\n'},
        {"pyproject.toml": BROKEN_TOML},
    )
    result = t1_config.run(pair)
    assert result.status == "completed"
    assert result.evidence == ()
    artifact = _artifact(pair)
    assert "pyproject.toml" in artifact["parse_failures"]
    assert artifact["candidate"]["ini_files"]["pyproject.toml"]["read"] is False


def test_config_reads_percent_in_addopts_verbatim(tmp_path):
    """A `%` in addopts is plain text to iniconfig, same as it was to RawConfigParser.

    An interpolating parser treats `%` as a format placeholder; RawConfigParser
    and iniconfig both do no interpolation, so this value reads the same on
    either and the swap changes nothing here. The pin is what shows that.
    """
    ini = '[pytest]\naddopts = -k "not x%y"\n'
    pair = _tree_pair(tmp_path, {"pytest.ini": ini}, {"pytest.ini": ini})
    result = t1_config.run(pair)
    assert result.status == "completed"
    assert result.evidence == ()
    artifact = _artifact(pair)
    assert artifact["candidate"]["selection"]["-k"] == ["not x%y"]


def test_config_ignores_selection_keys_with_nonmatching_case(tmp_path):
    """`AddOpts =` in `[pytest]` is not `addopts` to iniconfig, matching pytest.

    configparser lowercases every key it reads, so the old parser would have
    folded this line into the same key as a real `addopts` line. iniconfig
    preserves case, and so does pytest's own reading, so the line is invisible
    to selection: it neither becomes a key nor moves the winning file.
    """
    baseline_ini = "[pytest]\ntestpaths = tests\n"
    candidate_ini = baseline_ini + 'AddOpts = -k "not x"\n'
    pair = _tree_pair(tmp_path, {"pytest.ini": baseline_ini}, {"pytest.ini": candidate_ini})
    result = t1_config.run(pair)
    assert result.status == "completed"
    assert result.evidence == ()
    artifact = _artifact(pair)
    assert artifact["candidate"]["ini_files"]["pytest.ini"]["keys"] == {"testpaths": ["tests"]}
    assert "addopts" not in artifact["candidate"]["selection"]
    assert "-k" not in artifact["candidate"]["selection"]


def test_config_degrades_on_unparseable_candidate_ini(tmp_path):
    """A candidate `pytest.ini` iniconfig cannot parse degrades, not crashes.

    A key line before any section header is unparseable to iniconfig
    (`ParseError: no section header defined`), and the except tuple has to
    name `iniconfig.ParseError` or this crashes the run instead of degrading
    it, the same as `test_config_degrades_on_unparseable_candidate_config`
    covers for the tomllib side.
    """
    broken = "addopts = -k foo\n[pytest]\ntestpaths = tests\n"
    pair = _tree_pair(tmp_path, {}, {"pytest.ini": broken})
    result = t1_config.run(pair)
    assert result.status == "completed"
    assert result.evidence == ()
    artifact = _artifact(pair)
    assert "pytest.ini" in artifact["parse_failures"]
    assert artifact["candidate"]["ini_files"]["pytest.ini"]["read"] is False
    assert artifact["candidate"]["winning_file"] is None


def test_config_infra_on_unparseable_baseline_ini(tmp_path):
    """The same unparseable shape on the baseline side raises, naming the file.

    Skeptic seeded that tree, so an ini file it cannot read leaves nothing to
    compare against, the same rule `test_config_infra_error_on_unparseable_baseline_config`
    already pins for a broken `pyproject.toml`.
    """
    broken = "addopts = -k foo\n[pytest]\ntestpaths = tests\n"
    pair = _tree_pair(
        tmp_path, {"pytest.ini": broken}, {"pytest.ini": "[pytest]\ntestpaths = tests\n"})
    with pytest.raises(SkepticInfraError, match="pytest.ini"):
        t1_config.run(pair)


def test_config_pins_duplicate_key_handling(tmp_path):
    """Two `addopts` lines in one section: iniconfig 2.3 raises, pinned here.

    Measured directly against `iniconfig.IniConfig("<t>", data=text)`: it
    raises `ParseError: duplicate name 'addopts'`. `configparser.RawConfigParser
    (strict=False)` merges the two silently and keeps the last value.
    iniconfig treats it as unparseable instead, a stricter behavior than the
    parser this check used to run, so the file degrades like any other
    candidate-side parse failure. The pin is here so a future iniconfig
    release that stops raising on this shows up as a failing test instead of
    a silent behavior change.
    """
    dup = '[pytest]\naddopts = -k "first"\naddopts = -k "second"\n'
    pair = _tree_pair(tmp_path, {}, {"pytest.ini": dup})
    result = t1_config.run(pair)
    assert result.status == "completed"
    assert result.evidence == ()
    artifact = _artifact(pair)
    assert "duplicate" in artifact["parse_failures"]["pytest.ini"]
    assert artifact["candidate"]["ini_files"]["pytest.ini"]["read"] is False


def test_config_evidence_carries_the_covered_nodeids(tmp_path):
    """`--deselect` names nodeids, so the entry carries them.

    Task 8's ladder asks whether a config change covers the ids that went
    missing, and parsing them back out of `detail`, which is prose, is not an
    interface. `-k` and `-m` are keyword and marker expressions rather than
    ids, so nothing is lifted out of those.
    """
    deselected = ("tests/test_minirepo.py::test_parse_range_basic",
                  "tests/test_minirepo.py::test_parse_range_wide")
    pair = _tree_pair(
        tmp_path,
        {"pyproject.toml": '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n'},
        {"pyproject.toml": '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n'
                           f'addopts = "--deselect {deselected[0]} '
                           f'--deselect {deselected[1]}"\n'},
    )
    result = t1_config.run(pair)
    assert len(result.evidence) == 1
    entry = result.evidence[0]
    assert entry.nodeids == deselected
    assert _artifact(pair)["nodeids"] == list(deselected)
