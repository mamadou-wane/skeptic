import pytest

from skeptic.builder_tools import ToolContext, dispatch_tool
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
