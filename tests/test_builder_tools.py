import pytest

from skeptic.builder_tools import TOOL_DEFS, ToolContext, dispatch_tool
from skeptic.sandbox import ExecResult
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
    spec = make_task_spec(allowed_paths=["pkg/"])
    return ToolContext(workspace=ws, session=FakeSession(), spec=spec)


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
    assert out.suite_green and "passed" in out.text


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
    assert not out.suite_green


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
    assert not out.suite_green


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
    assert not out.suite_green
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
