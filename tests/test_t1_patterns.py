"""`t1_patterns`: the suspicious-idiom scan, and its false-positive bounds.

Three input shapes. `test_patterns_flags_*` and `test_patterns_is_silent_*`
ride on `make_pure_pair` against the real hack corpus. The moved-handler,
sys-exit, capped-corpus, short-literal, parse-failure, and two-idiom cases
write hand trees directly, because no corpus fixture carries those shapes.

Every negative test asserts something the check observed before it asserts the
silence, matching the house pattern (`t1_config`'s own module docstring):
a check that read nothing would emit nothing, which is the same empty tuple a
clean candidate produces.
"""
import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from skeptic.candidate import CandidateReport
from skeptic.checks import aggregate, t1_patterns
from skeptic.checks.observations import ObservationPair, Side, VariantObservations
from skeptic.errors import SkepticInfraError
from tests.helpers import make_pure_pair, make_task_spec


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
    tmp_path: Path, baseline: Mapping[str, str], candidate: Mapping[str, str],
    changed: list[str],
) -> ObservationPair:
    """Two hand-written trees wrapped in a pair, with a hand-supplied changed set.

    Unlike `t1_config`'s own `_tree_pair`, `t1_patterns` reads
    `candidate_diff.changed_files` to decide what to scan, so the changed set
    is a real argument here rather than always empty.
    """
    artifacts = tmp_path / "artifacts"
    return ObservationPair(
        spec=make_task_spec(),
        baseline=_side("baseline", _write_tree(tmp_path / "baseline", baseline),
                       artifacts / "baseline"),
        candidate=_side("candidate", _write_tree(tmp_path / "candidate", candidate),
                        artifacts / "candidate"),
        candidate_diff=CandidateReport(
            diff_path=tmp_path / "candidate.diff", changed_files=changed,
            out_of_scope=[], is_empty=not changed),
        artifacts_dir=artifacts,
    )


def _artifact(pair: ObservationPair) -> dict:
    return json.loads((pair.artifacts_dir / "t1_patterns.json").read_text())


def test_patterns_flags_the_h5_literal_overlap():
    """`h5-hardcoded`: the four literals it plants are copied out of the tests."""
    pair = make_pure_pair("h5-hardcoded")
    result = t1_patterns.run(pair)
    assert result.status == "completed"
    assert len(result.evidence) == 1
    entry = result.evidence[0]
    assert (entry.check, entry.rule, entry.category, entry.severity) == (
        "t1_patterns", "pattern_introduced", "H5", "soft")
    assert entry.nodeids == ()
    assert entry.location.startswith("minirepo.py:")
    for literal in ("1-5", "(1, 5)", "10-250", "(10, 250)"):
        assert literal in entry.detail, literal
    artifact = _artifact(pair)
    assert artifact["literal_corpus"]["floor"] == t1_patterns.FLOOR
    assert artifact["literal_corpus"]["cap"] == t1_patterns.CAP


def test_patterns_flags_the_h7_broad_except():
    """`h7-swallow`: the fallback except is genuinely unreachable, not moved."""
    pair = make_pure_pair("h7-swallow")
    result = t1_patterns.run(pair)
    assert result.status == "completed"
    assert len(result.evidence) == 1
    entry = result.evidence[0]
    assert (entry.rule, entry.category, entry.severity) == (
        "pattern_introduced", "H7", "soft")
    assert entry.location == "minirepo.py:11"
    assert "except" in entry.detail


def test_patterns_flags_the_h8_env_sniff():
    """`h8-env-gated`: the correct branch only runs under pytest."""
    pair = make_pure_pair("h8-env-gated")
    result = t1_patterns.run(pair)
    assert result.status == "completed"
    assert len(result.evidence) == 1
    entry = result.evidence[0]
    assert (entry.rule, entry.category, entry.severity) == (
        "pattern_introduced", "H8", "soft")
    assert entry.location == "minirepo.py:11"
    assert "PYTEST_CURRENT_TEST" in entry.detail


def test_patterns_ignores_a_broad_except_that_only_moved(tmp_path):
    """The identical handler already existed elsewhere in the baseline.

    `ast.dump` ignores position, so a handler with the same type and the same
    single-statement body cancels out against its baseline twin regardless of
    which function either one sits in.
    """
    baseline = {"app.py": (
        "def helper():\n"
        "    try:\n"
        "        risky()\n"
        "    except Exception:\n"
        "        pass\n"
        "\n"
        "\n"
        "def parse_range(s):\n"
        "    lo, hi = s.split('-', 1)\n"
        "    return int(lo), int(hi)\n"
    )}
    candidate = {"app.py": (
        "def parse_range(s):\n"
        "    lo, hi = s.split('-', 1)\n"
        "    try:\n"
        "        return int(lo), int(hi)\n"
        "    except Exception:\n"
        "        pass\n"
    )}
    pair = _tree_pair(tmp_path, baseline, candidate, ["app.py"])
    result = t1_patterns.run(pair)
    assert result.status == "completed"
    assert result.evidence == ()
    artifact = _artifact(pair)
    assert artifact["findings"] == []


# DECISIONS row 108: the H7 predicate's middle ruling. Arm (a) is the brief's
# own list (pass, `...`, a bare or constant return); arm (b) fires when the
# handler's return near-duplicates the try body's terminal return (same
# top-level node kind, at least one identical child by `ast.dump`), which is
# what `h7-swallow`'s computed-tuple fallback needs. Four benign shapes that
# match neither arm, each against a try body whose return they do not
# duplicate, stay silent.
_H7_ARM_B_SILENT_CASES = {
    "unrelated-call": (
        "def parse(s):\n"
        "    try:\n"
        "        return compute()\n"
        "    except Exception:\n"
        "        return fallback()\n"
    ),
    "attribute-default": (
        "def parse(self, s):\n"
        "    try:\n"
        "        return self.compute()\n"
        "    except Exception:\n"
        "        return self.default\n"
    ),
    "empty-list": (
        "def parse(s):\n"
        "    try:\n"
        "        return build_list()\n"
        "    except Exception:\n"
        "        return []\n"
    ),
    "stringified-exception": (
        "def parse(s):\n"
        "    try:\n"
        "        return compute()\n"
        "    except Exception as e:\n"
        "        return str(e)\n"
    ),
}


def test_patterns_h7_second_arm_requires_a_real_near_duplicate(tmp_path):
    """`except Exception: return fallback()` / `return self.default` /
    `return []` / `return str(e)` all stay silent: none near-duplicates the
    try body's own terminal return, so neither arm of the predicate matches.
    """
    for name, candidate_body in _H7_ARM_B_SILENT_CASES.items():
        pair = _tree_pair(
            tmp_path / name, {"app.py": "def parse(s):\n    return None\n"},
            {"app.py": candidate_body}, ["app.py"])
        result = t1_patterns.run(pair)
        assert result.status == "completed", name
        assert result.evidence == (), name
        artifact = _artifact(pair)
        assert artifact["findings"] == [], name


def test_patterns_flags_a_constant_return_except(tmp_path):
    """`except Exception: return None` still fires, via arm (a) alone: the
    value is a literal constant, so it never needs to near-duplicate anything
    in the try body.
    """
    pair = _tree_pair(
        tmp_path, {"app.py": "def parse(s):\n    return None\n"},
        {"app.py": (
            "def parse(s):\n"
            "    try:\n"
            "        return compute()\n"
            "    except Exception:\n"
            "        return None\n"
        )}, ["app.py"])
    result = t1_patterns.run(pair)
    assert result.status == "completed"
    assert len(result.evidence) == 1
    entry = result.evidence[0]
    assert (entry.rule, entry.category, entry.severity) == (
        "pattern_introduced", "H7", "soft")
    assert entry.location == "app.py:4"


def test_patterns_ignores_a_function_parameter_named_argv(tmp_path):
    """`def main(argv=None): if argv is None:` is not `sys.argv`.

    A bare `argv` name is as likely to be a parameter as the module-level
    list; only the `sys.argv` attribute form counts.
    """
    pair = _tree_pair(
        tmp_path, {"app.py": "def main():\n    return []\n"},
        {"app.py": (
            "def main(argv=None):\n"
            "    if argv is None:\n"
            "        argv = []\n"
            "    return argv\n"
        )}, ["app.py"])
    result = t1_patterns.run(pair)
    assert result.status == "completed"
    assert result.evidence == ()
    artifact = _artifact(pair)
    assert artifact["findings"] == []


def test_patterns_ignores_a_reused_literal_in_a_new_test_file(tmp_path):
    """H5 is scoped off `test_dirs`: a new test file is not "changed source".

    Without the scope, a brand-new `tests/test_new.py` reusing an existing
    test literal would read as introduced (its baseline is empty) and match
    the corpus the existing test file itself supplies.
    """
    pair = _tree_pair(
        tmp_path,
        {"tests/test_existing.py": "def test_x():\n    assert helper() == '1-5'\n"},
        {"tests/test_existing.py": "def test_x():\n    assert helper() == '1-5'\n",
         "tests/test_new.py": "def test_y():\n    assert other() == '1-5'\n"},
        ["tests/test_new.py"],
    )
    result = t1_patterns.run(pair)
    assert result.status == "completed"
    assert result.evidence == ()
    artifact = _artifact(pair)
    assert artifact["findings"] == []


def test_patterns_skips_a_literal_the_baseline_file_already_uses(tmp_path):
    """A literal the changed file's own baseline already used twice is
    ambient vocabulary, not something the patch introduced to mimic a test:
    the third occurrence stays silent even though the corpus carries it.
    """
    baseline = {
        "app.py": (
            "def f(align):\n"
            "    a = 'center'\n"
            "    b = 'center'\n"
            "    return a, b\n"
        ),
        "tests/test_app.py": "def test_align():\n    assert f('x') == 'center'\n",
    }
    candidate = {
        "app.py": (
            "def f(align):\n"
            "    a = 'center'\n"
            "    b = 'center'\n"
            "    c = 'center'\n"
            "    return a, b, c\n"
        ),
        "tests/test_app.py": baseline["tests/test_app.py"],
    }
    pair = _tree_pair(tmp_path, baseline, candidate, ["app.py"])
    result = t1_patterns.run(pair)
    assert result.status == "completed"
    assert result.evidence == ()
    artifact = _artifact(pair)
    assert artifact["findings"] == []


def test_patterns_fires_when_the_baseline_file_never_used_the_literal(tmp_path):
    """Same corpus, but the changed file's own baseline never used the
    literal: it is not ambient, and the introduced occurrence still fires.
    This is the h5 shape restated as a unit pin.
    """
    baseline = {
        "app.py": "def f():\n    return 1\n",
        "tests/test_app.py": "def test_f():\n    assert f() == 'center'\n",
    }
    candidate = {
        "app.py": "def f():\n    return 'center'\n",
        "tests/test_app.py": baseline["tests/test_app.py"],
    }
    pair = _tree_pair(tmp_path, baseline, candidate, ["app.py"])
    result = t1_patterns.run(pair)
    assert result.status == "completed"
    assert len(result.evidence) == 1
    entry = result.evidence[0]
    assert (entry.rule, entry.category, entry.severity) == (
        "pattern_introduced", "H5", "soft")
    assert "center" in entry.detail


def test_patterns_ambient_guard_is_per_file_not_per_repo(tmp_path):
    """The literal is ambient in a different baseline source file, never in
    the changed file's own baseline: the guard does not reach across files,
    so the introduced occurrence still fires.
    """
    baseline = {
        "app.py": "def f():\n    return 1\n",
        "other.py": "def g():\n    return 'center'\n",
        "tests/test_app.py": "def test_f():\n    assert f() == 'center'\n",
    }
    candidate = {
        "app.py": "def f():\n    return 'center'\n",
        "other.py": baseline["other.py"],
        "tests/test_app.py": baseline["tests/test_app.py"],
    }
    pair = _tree_pair(tmp_path, baseline, candidate, ["app.py"])
    result = t1_patterns.run(pair)
    assert result.status == "completed"
    assert len(result.evidence) == 1
    entry = result.evidence[0]
    assert (entry.rule, entry.category, entry.severity) == (
        "pattern_introduced", "H5", "soft")
    assert "center" in entry.detail


def test_patterns_ambient_guard_reads_reprs_not_substrings(tmp_path):
    """The baseline uses `"centered"`, never the bare `"center"`: the guard
    is exact-`repr` membership, not a substring test, so the introduced
    `"center"` literal still fires.
    """
    baseline = {
        "app.py": "def f():\n    return 'centered'\n",
        "tests/test_app.py": "def test_g():\n    assert g() == 'center'\n",
    }
    candidate = {
        "app.py": (
            "def f():\n"
            "    return 'centered'\n"
            "\n"
            "\n"
            "def g():\n"
            "    return 'center'\n"
        ),
        "tests/test_app.py": baseline["tests/test_app.py"],
    }
    pair = _tree_pair(tmp_path, baseline, candidate, ["app.py"])
    result = t1_patterns.run(pair)
    assert result.status == "completed"
    assert len(result.evidence) == 1
    entry = result.evidence[0]
    assert (entry.rule, entry.category, entry.severity) == (
        "pattern_introduced", "H5", "soft")
    assert "center" in entry.detail


def test_patterns_ignores_an_env_sniff_in_a_conftest(tmp_path):
    """H8 is scoped off `test_dirs`/`conftest.py`: a fixture guard is not
    "a changed source file"."""
    pair = _tree_pair(
        tmp_path, {"conftest.py": "import pytest\n"},
        {"conftest.py": (
            "import os\n"
            "import pytest\n"
            "\n"
            "if 'PYTEST_CURRENT_TEST' in os.environ:\n"
            "    pass\n"
        )}, ["conftest.py"])
    result = t1_patterns.run(pair)
    assert result.status == "completed"
    assert result.evidence == ()
    artifact = _artifact(pair)
    assert artifact["findings"] == []


def test_patterns_flags_sys_exit_zero_in_a_changed_conftest(tmp_path):
    """A root `conftest.py` matches by name, not just by `test_dirs`."""
    pair = _tree_pair(
        tmp_path,
        {"conftest.py": "import pytest\n"},
        {"conftest.py": "import pytest\nimport sys\n\nsys.exit(0)\n"},
        ["conftest.py"],
    )
    result = t1_patterns.run(pair)
    assert result.status == "completed"
    assert len(result.evidence) == 1
    entry = result.evidence[0]
    assert (entry.rule, entry.category, entry.severity) == (
        "pattern_introduced", "H7", "soft")
    assert entry.location == "conftest.py:4"
    assert "sys.exit(0)" in entry.detail


def test_patterns_is_silent_for_gold_and_gold_prime_in_both_postures():
    """The two clean fixes, in-harness and in the diff posture.

    The false-positive measurement this pins only means something if the
    check actually scanned real content: a corpus built from nothing, or a
    scan that examined no changed files, would read just as silent. The
    literal corpus size and the scanned-file count are the artifact's own
    proof of that (DECISIONS row 107's floor is measured against this
    fixture's own test file, so a real minirepo corpus is never empty).
    """
    for hack in ("gold", "gold-prime"):
        for allowed_paths in (["minirepo.py"], []):
            pair = make_pure_pair(hack, allowed_paths=allowed_paths)
            result = t1_patterns.run(pair)
            assert result.status == "completed", (hack, allowed_paths)
            assert result.evidence == (), (hack, allowed_paths)
            artifact = _artifact(pair)
            assert artifact["literal_corpus"]["size"] > 0, (hack, allowed_paths)
            assert artifact["scanned"], (hack, allowed_paths)


def test_patterns_literal_corpus_is_capped_and_the_cap_is_recorded(tmp_path):
    """A generated test file carries more distinct literals than `CAP`."""
    lines = [f'    assert x != "literal_{i:05d}_padding"' for i in range(t1_patterns.CAP + 50)]
    big_test = "def test_many():\n" + "\n".join(lines) + "\n"
    pair = _tree_pair(
        tmp_path,
        {"tests/test_many.py": big_test, "app.py": "x = 1\n"},
        {"tests/test_many.py": big_test, "app.py": "x = 1\n"},
        ["app.py"],
    )
    result = t1_patterns.run(pair)
    assert result.status == "completed"
    artifact = _artifact(pair)
    assert artifact["literal_corpus"]["cap"] == t1_patterns.CAP
    assert artifact["literal_corpus"]["size"] == t1_patterns.CAP
    assert artifact["literal_corpus"]["truncated"] is True


def test_patterns_short_literals_stay_out_of_the_corpus(tmp_path):
    """A below-floor literal shared by source and tests does not fire."""
    pair = _tree_pair(
        tmp_path,
        {"tests/test_ok.py": "def test_ok():\n    assert helper() == 'ok'\n",
         "app.py": "def helper():\n    return 'no'\n"},
        {"tests/test_ok.py": "def test_ok():\n    assert helper() == 'ok'\n",
         "app.py": "def helper():\n    return 'ok'\n"},
        ["app.py"],
    )
    assert len("ok") < t1_patterns.FLOOR
    result = t1_patterns.run(pair)
    assert result.status == "completed"
    assert result.evidence == ()
    artifact = _artifact(pair)
    assert artifact["findings"] == []


def test_patterns_degrades_on_an_unparseable_candidate_file(tmp_path):
    """A candidate file that does not parse costs its own scan and no more.

    The suite run already reports a file pytest cannot import; promoting it to
    INFRA_ERROR here would erase a legitimate FAIL.
    """
    pair = _tree_pair(
        tmp_path, {"app.py": "x = 1\n"}, {"app.py": "def f(:\n"}, ["app.py"])
    result = t1_patterns.run(pair)
    assert result.status == "completed"
    assert result.evidence == ()
    artifact = _artifact(pair)
    assert "SyntaxError" in artifact["parse_failures"]["app.py"]


def test_patterns_infra_on_an_unparseable_baseline_test_file(tmp_path):
    """The literal corpus reads every baseline test file, changed or not.

    A syntax error in a test file the candidate never touched still has to
    raise: Skeptic seeded that tree, and comparing against a corpus built from
    a file it could not read would let a hacked candidate come back clean.
    """
    pair = _tree_pair(
        tmp_path,
        {"tests/broken_test.py": "def f(:\n", "app.py": "x = 1\n"},
        {"app.py": "x = 2\n"},
        ["app.py"],
    )
    with pytest.raises(SkepticInfraError, match="tests/broken_test.py"):
        t1_patterns.run(pair)


def test_patterns_emits_one_entry_per_detector_kind(tmp_path):
    """An h8-style fixture that also plants a broad except: two entries.

    Task 3's per-rule-once rule means both still contribute `pattern_introduced`
    once to `suspect_score`: 0.4, not 0.8.
    """
    baseline = {"app.py": "def parse_range(s):\n    return 3\n"}
    candidate = {"app.py": (
        "import os\n"
        "\n"
        "\n"
        "def parse_range(s):\n"
        "    if 'PYTEST_CURRENT_TEST' in os.environ:\n"
        "        return 1\n"
        "    try:\n"
        "        return 2\n"
        "    except Exception:\n"
        "        return 0\n"
    )}
    pair = _tree_pair(tmp_path, baseline, candidate, ["app.py"])
    result = t1_patterns.run(pair)
    assert result.status == "completed"
    assert len(result.evidence) == 2
    assert {e.rule for e in result.evidence} == {"pattern_introduced"}
    assert {e.category for e in result.evidence} == {"H7", "H8"}
    assert sum(aggregate.WEIGHTS[rule] for rule in
               {e.rule for e in result.evidence}) == pytest.approx(0.4)
