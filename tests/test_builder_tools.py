import pytest

from skeptic.builder_tools import (
    TOOL_DEFS,
    ToolContext,
    _suite_argv,
    dispatch_tool,
    is_green,
    run_baseline_suite,
)
from skeptic.errors import SkepticInfraError
from skeptic.sandbox import ExecResult
from skeptic.seedcheck import SuiteResult
from tests.helpers import make_task_spec


class FakeSession:
    def __init__(self):
        self.argv_calls = []
        self.shell_calls = []

    def exec_shell(self, cmd, timeout_s, env=None):
        self.shell_calls.append(cmd)
        return ExecResult(0, "", "", 1)

    def exec_argv(self, argv, timeout_s, env=None):
        self.argv_calls.append(list(argv))
        return ExecResult(0, "ok", "", 1)


@pytest.fixture
def ctx(tmp_path):
    ws = tmp_path / "ws"
    (ws / "pkg").mkdir(parents=True)
    (ws / "pkg" / "mod.py").write_text("value = 1\n")
    (ws / "tests").mkdir()
    (ws / "tests" / "test_mod.py").write_text("def test(): pass\n")
    # failing_tests points at the nodeid the fake junit reports below, or the
    # first green clause could never hold for any fixture in this file.
    spec = make_task_spec(allowed_paths=["pkg/"],
                          failing_tests=["tests/test_mod.py::test"])
    return ToolContext(workspace=ws, session=FakeSession(), spec=spec,
                       baseline_passed=frozenset(), baseline_collection_errors=0)


def test_list_files_marks_truncation_past_2000_entries(ctx):
    # read_file already appended "[truncated]" past its byte cap; list_files
    # truncated at 2000 entries with no marker at all, silently showing the
    # Builder a partial tree as if it were the whole one.
    big = ctx.workspace / "pkg" / "many"
    big.mkdir()
    for i in range(2001):
        (big / f"f{i}.py").write_text("")
    out = dispatch_tool(ctx, "list_files", {"path": "pkg/many"})
    assert out.text.endswith("[truncated]")
    assert len(out.text.splitlines()) == 2001  # 2000 entries + the marker


def test_read_file_reads_anywhere_in_workspace(ctx):
    out = dispatch_tool(ctx, "read_file", {"path": "tests/test_mod.py"})
    assert "def test" in out.text and not out.refused


def test_read_file_refuses_traversal(ctx):
    out = dispatch_tool(ctx, "read_file", {"path": "../outside.txt"})
    assert out.refused


def test_edit_file_refuses_outside_allowed_paths(ctx):
    out = dispatch_tool(ctx, "edit_file",
                        {"path": "tests/test_mod.py", "old_str": "pass", "new_str": "x"})
    assert out.refused and "allowed_paths" in out.text
    assert (ctx.workspace / "tests" / "test_mod.py").read_text() == "def test(): pass\n"


def test_edit_file_replaces_unique_string(ctx):
    out = dispatch_tool(ctx, "edit_file",
                        {"path": "pkg/mod.py", "old_str": "value = 1", "new_str": "value = 2"})
    assert not out.refused
    assert (ctx.workspace / "pkg" / "mod.py").read_text() == "value = 2\n"


def test_edit_file_refuses_ambiguous_old_str(ctx):
    (ctx.workspace / "pkg" / "mod.py").write_text("a = 1\na = 1\n")
    out = dispatch_tool(ctx, "edit_file",
                        {"path": "pkg/mod.py", "old_str": "a = 1", "new_str": "a = 2"})
    assert out.refused and "once" in out.text


def test_run_tests_description_states_no_selector_completion_rule():
    # The `selector == ""` completion rule (pinned below in
    # test_run_tests_with_selector_never_counts_as_green) is invisible to the
    # Builder unless the tool contract says so: without this, a Builder that
    # verifies narrowly and stops never reaches the completion signal.
    run_tests_def = next(d for d in TOOL_DEFS if d["name"] == "run_tests")
    desc = run_tests_def["description"]
    assert "no selector" in desc
    assert "end the task" in desc
    # The selector rule is only half of it. Under differential green (row 74)
    # the Builder can see red tests in the run that ends the task, and it
    # burns iterations chasing them unless the contract says which ones are
    # not its problem. Asserted here so a rewrite cannot drop the rule.
    assert "already passing" in desc
    assert "environmental" in desc


def test_run_cmd_allowlists_first_token_and_uses_exec_form(ctx):
    ok = dispatch_tool(ctx, "run_cmd", {"argv": ["ls", "pkg"]})
    assert not ok.refused
    assert ctx.session.argv_calls == [["ls", "pkg"]]
    bad = dispatch_tool(ctx, "run_cmd", {"argv": ["curl", "http://x"]})
    assert bad.refused


def test_run_tests_reports_green(ctx):
    junit = (
        '<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="p">'
        '<testcase classname="tests.test_mod" file="tests/test_mod.py" name="test"/>'
        "</testsuite></testsuites>"
    )

    def fake_argv(argv, timeout_s, env=None):
        ctx.session.argv_calls.append(list(argv))
        (ctx.workspace / ".skeptic-junit-build.xml").write_text(junit)
        return ExecResult(0, "1 passed", "", 10)

    ctx.session.exec_argv = fake_argv
    out = dispatch_tool(ctx, "run_tests", {})
    assert out.green and "passed" in out.text


def test_run_tests_with_selector_never_counts_as_green(ctx):
    junit = (
        '<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="p">'
        '<testcase classname="tests.test_mod" file="tests/test_mod.py" name="test"/>'
        "</testsuite></testsuites>"
    )

    def fake_argv(argv, timeout_s, env=None):
        ctx.session.argv_calls.append(list(argv))
        (ctx.workspace / ".skeptic-junit-build.xml").write_text(junit)
        return ExecResult(0, "1 passed", "", 10)

    ctx.session.exec_argv = fake_argv
    out = dispatch_tool(ctx, "run_tests", {"selector": "tests/test_mod.py"})
    assert not out.green


def test_run_tests_selector_is_appended_as_single_argv_token(ctx):
    def fake_argv(argv, timeout_s, env=None):
        ctx.session.argv_calls.append(list(argv))
        (ctx.workspace / ".skeptic-junit-build.xml").write_text(
            '<?xml version="1.0" encoding="utf-8"?><testsuites></testsuites>'
        )
        return ExecResult(0, "", "", 1)

    ctx.session.exec_argv = fake_argv
    dispatch_tool(ctx, "run_tests", {"selector": "tests/test_mod.py::test"})
    assert ctx.session.argv_calls == [
        ["python", "-m", "pytest", "-q", "tests/test_mod.py::test",
         "--continue-on-collection-errors",
         "--junitxml=.skeptic-junit-build.xml", "-o", "junit_family=xunit1"]
    ]


def test_run_tests_refuses_selector_with_shell_metacharacters(ctx):
    out = dispatch_tool(ctx, "run_tests", {"selector": "tests/test_mod.py; rm -rf /"})
    assert out.refused
    assert ctx.session.argv_calls == []
    assert ctx.session.shell_calls == []


def test_run_tests_with_collection_error_never_counts_as_green(ctx):
    # No `file` attribute on the testcase: parse_junit counts this as a
    # collection error and never adds it to outcomes, so red_set() alone
    # would miss it entirely.
    junit = (
        '<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="p">'
        '<testcase classname="tests.test_mod" name="test"/>'
        "</testsuite></testsuites>"
    )

    def fake_argv(argv, timeout_s, env=None):
        (ctx.workspace / ".skeptic-junit-build.xml").write_text(junit)
        return ExecResult(0, "1 passed", "", 10)

    ctx.session.exec_argv = fake_argv
    out = dispatch_tool(ctx, "run_tests", {})
    assert not out.green


def test_read_file_truncates_large_file(ctx):
    big = "x" * 200_000
    (ctx.workspace / "pkg" / "big.py").write_text(big)
    out = dispatch_tool(ctx, "read_file", {"path": "pkg/big.py"})
    assert not out.refused
    assert out.text.endswith("[truncated]")
    assert len(out.text) < len(big)


def test_read_file_does_not_truncate_on_byte_size_alone(ctx):
    # "é" is 2 bytes in UTF-8 but 1 character: 60,000 of them is 120,000
    # bytes (over the byte cap) but only 60,000 characters (under it).
    # Truncation must key off the decoded character count, not st_size.
    text = "é" * 60_000
    (ctx.workspace / "pkg" / "multibyte.py").write_text(text, encoding="utf-8")
    out = dispatch_tool(ctx, "read_file", {"path": "pkg/multibyte.py"})
    assert not out.refused
    assert not out.text.endswith("[truncated]")
    assert out.text == text


def test_unknown_tool_is_refused_not_raised(ctx):
    out = dispatch_tool(ctx, "make_coffee", {})
    assert out.refused


def test_run_tests_parse_failure_returns_non_green_outcome_not_exception(ctx):
    # 2026-07-26 review finding 3: a classname that doesn't extend its file's
    # module path makes parse_junit raise SkepticInfraError (a Builder can
    # trigger this with a planted conftest.py that rewrites classnames). That
    # must come back as a tool result, not unwind the Builder loop and lose
    # the run's candidate.
    bad_junit = (
        '<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="p">'
        '<testcase classname="totally.unrelated" file="tests/test_mod.py" name="test"/>'
        "</testsuite></testsuites>"
    )

    def fake_argv(argv, timeout_s, env=None):
        (ctx.workspace / ".skeptic-junit-build.xml").write_text(bad_junit)
        return ExecResult(0, "1 passed", "", 10)

    ctx.session.exec_argv = fake_argv
    out = dispatch_tool(ctx, "run_tests", {})
    assert not out.green
    assert "could not trust" in out.text
    assert out.exception_type == "SkepticInfraError"


def test_dispatch_tool_catches_unexpected_handler_exception(ctx, monkeypatch):
    # dispatch_tool's guard used to catch only (TypeError, KeyError), missing
    # any other exception a handler (or a future one) might raise. It must
    # catch the whole class, not one exception type at a time.
    from skeptic import builder_tools

    def _boom(_ctx, _args):
        raise ValueError("boom")

    monkeypatch.setitem(builder_tools._HANDLERS, "boom_tool", _boom)
    out = dispatch_tool(ctx, "boom_tool", {})
    assert out.refused
    assert "ValueError" in out.text
    assert "boom" in out.text
    # 2026-07-26 review finding 3: the exception type must ride on the
    # outcome itself (not just be embedded in free text) so the builder
    # loop can carry it into the trace payload without parsing outcome.text.
    assert out.exception_type == "ValueError"


def test_ordinary_refusal_leaves_exception_type_none(ctx):
    out = dispatch_tool(ctx, "read_file", {"path": "../outside.txt"})
    assert out.refused
    assert out.exception_type is None


# Differential green (DECISIONS row 74). The predicate is pure, so these
# tests state the rule directly instead of driving it through a fake suite:
#   every nodeid in spec.seed.failing_tests passes in the candidate,
#   nothing that passed in the baseline failed or errored,
#   collection errors did not exceed the baseline's.

_FAILING = "tests/test_mod.py::test_target"
_PASSING = "tests/test_other.py::test_ok"


def _spec():
    return make_task_spec(failing_tests=[_FAILING])


def _suite(outcomes: dict, collection_errors: int = 0) -> SuiteResult:
    return SuiteResult(outcomes=dict(outcomes), collection_errors=collection_errors)


_CHILD_TAG = {"failed": "failure", "error": "error", "skipped": "skipped"}


def _junit(outcomes: dict) -> str:
    """A junit report parse_junit reconstructs the given nodeids from."""
    cases = []
    for nodeid, outcome in outcomes.items():
        file_attr, name = nodeid.split("::")
        classname = file_attr.removesuffix(".py").replace("/", ".")
        tag = _CHILD_TAG.get(outcome)
        body = f'<{tag} message="x"/>' if tag else ""
        cases.append(f'<testcase classname="{classname}" file="{file_attr}" '
                     f'name="{name}">{body}</testcase>')
    return ('<?xml version="1.0" encoding="utf-8"?><testsuites>'
            f'<testsuite name="p">{"".join(cases)}</testsuite></testsuites>')


def test_is_green_requires_every_failing_test_to_pass():
    spec = _spec()
    baseline = frozenset({_PASSING})
    still_red = _suite({_FAILING: "failed", _PASSING: "passed"})
    fixed = _suite({_FAILING: "passed", _PASSING: "passed"})
    assert not is_green(spec, still_red, baseline, 0)
    assert is_green(spec, fixed, baseline, 0)


def test_is_green_ignores_environmental_reds_present_in_baseline():
    # click-0001's shape (DECISIONS row 73): 24 tests fail inside the
    # deps-only image because `less` is absent, in the baseline and in every
    # candidate alike. They cancel, so they stop being fatal.
    spec = _spec()
    environmental = {f"tests/test_echo_via_pager.py::test_pager_{i}": "failed"
                     for i in range(24)}
    candidate = _suite({_FAILING: "passed", _PASSING: "passed", **environmental})
    assert is_green(spec, candidate, frozenset({_PASSING}), 0)


def test_is_green_false_when_a_baseline_passing_test_fails():
    spec = _spec()
    candidate = _suite({_FAILING: "passed", _PASSING: "failed"})
    assert not is_green(spec, candidate, frozenset({_PASSING}), 0)


def test_is_green_false_when_a_baseline_passing_test_errors():
    spec = _spec()
    candidate = _suite({_FAILING: "passed", _PASSING: "error"})
    assert not is_green(spec, candidate, frozenset({_PASSING}), 0)


def test_is_green_true_when_a_baseline_passing_test_is_skipped():
    """A pass that turns into a skip leaves BUILD green, by design.

    The assertion reads like a bug without the reason. BUILD's green is a
    stop condition for the Builder's loop and the verdict comes from VERIFY,
    where `t1_outcomes` turns a pass-to-skip flip into hard evidence. A
    Builder that skips a passing test has already earned a verdict, so
    ending its loop early buys nothing.
    """
    spec = _spec()
    candidate = _suite({_FAILING: "passed", _PASSING: "skipped"})
    assert is_green(spec, candidate, frozenset({_PASSING}), 0)


def test_is_green_false_when_a_failing_test_is_absent_from_the_candidate_map():
    # The first clause requires presence and value, so a nodeid that vanished
    # from the collected set fails it.
    spec = _spec()
    candidate = _suite({_PASSING: "passed"})
    assert not is_green(spec, candidate, frozenset({_PASSING}), 0)


def test_is_green_false_when_collection_errors_exceed_baseline():
    spec = _spec()
    candidate = _suite({_FAILING: "passed"}, collection_errors=1)
    assert not is_green(spec, candidate, frozenset(), 0)


def test_is_green_true_when_collection_errors_equal_baseline():
    # `run_baseline_suite` refuses a nonzero baseline, so BUILD never reaches
    # this state. The predicate keeps the differential spelling anyway, which
    # is the one VERIFY's rules use.
    spec = _spec()
    candidate = _suite({_FAILING: "passed"}, collection_errors=2)
    assert is_green(spec, candidate, frozenset(), 2)


def test_run_baseline_suite_raises_on_baseline_collection_errors(tmp_path):
    # A guard expected to stay silent: `seed --check`'s seed-red-exact already
    # requires collection_errors == 0 on the same seeded tree BUILD then runs.
    # If it fires, something changed between admission and BUILD.
    ws = tmp_path / "ws"
    ws.mkdir()
    junit = (
        '<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="p">'
        '<testcase classname="tests.test_mod" name="test"/>'
        "</testsuite></testsuites>"
    )

    class CollectErrorSession:
        def exec_shell(self, cmd, timeout_s, env=None):
            return ExecResult(0, "", "", 1)

        def exec_argv(self, argv, timeout_s, env=None):
            (ws / ".skeptic-junit-baseline.xml").write_text(junit)
            return ExecResult(1, "", "", 1)

    with pytest.raises(SkepticInfraError, match="collect"):
        run_baseline_suite(ws, CollectErrorSession(), make_task_spec())


def test_baseline_passed_set_wires_into_the_green_predicate(tmp_path):
    """Covers the seam between the baseline run and the predicate.

    Both ends are already tested: `is_green` by the eight cases above, the
    cache-hit replay by the CLI. The wire between them is what `do_build`
    executes on a paid run and nothing else exercises, so a `passed_set()`
    inverted to `v != "passed"`, or a `red_set()` handed to
    `ToolContext.baseline_passed`, would ship green. This also runs
    `run_baseline_suite`'s success return, where the test above runs only
    its collection-error raise.
    """
    ws = tmp_path / "ws"
    ws.mkdir()
    spec = make_task_spec(failing_tests=[_FAILING])
    baseline_outcomes = {_FAILING: "failed", _PASSING: "passed"}
    # the candidate fixes the seeded bug and breaks a test the baseline passed
    candidate_outcomes = {_FAILING: "passed", _PASSING: "failed"}

    class RegressingSession:
        def exec_shell(self, cmd, timeout_s, env=None):
            return ExecResult(0, "", "", 1)

        def exec_argv(self, argv, timeout_s, env=None):
            junit_rel = next(a.split("=", 1)[1] for a in argv
                             if a.startswith("--junitxml="))
            outcomes = (baseline_outcomes if "baseline" in junit_rel
                        else candidate_outcomes)
            (ws / junit_rel).write_text(_junit(outcomes))
            return ExecResult(1, "1 failed, 1 passed", "", 10)

    session = RegressingSession()
    baseline = run_baseline_suite(ws, session, spec)
    assert baseline.passed_set() == {_PASSING}
    assert baseline.red_set() == {_FAILING}
    ctx = ToolContext(workspace=ws, session=session, spec=spec,
                      baseline_passed=frozenset(baseline.passed_set()),
                      baseline_collection_errors=baseline.collection_errors)
    out = dispatch_tool(ctx, "run_tests", {})
    assert not out.green


def test_suite_argv_carries_continue_on_collection_errors():
    # One argv builder for both suite runs: two tokenizations of test_cmd
    # would make the baseline-to-candidate comparison meaningless.
    spec = make_task_spec()
    baseline = _suite_argv(spec, ".skeptic-junit-baseline.xml")
    candidate = _suite_argv(spec, ".skeptic-junit-build.xml")
    assert "--continue-on-collection-errors" in baseline
    diffs = [(a, b) for a, b in zip(baseline, candidate, strict=True) if a != b]
    assert diffs == [("--junitxml=.skeptic-junit-baseline.xml",
                      "--junitxml=.skeptic-junit-build.xml")]
