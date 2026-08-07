import inspect
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest

from skeptic.checks.observations import AdvCandidate
from skeptic.llm import SKEPTIC_MODEL
from skeptic.testgen import (
    SYSTEM_PROMPT,
    build_testgen_prompt,
    generate_candidates,
    one_hop_sources,
    parse_candidates,
    screen_imports,
)
from skeptic.trace import TraceWriter, config_hash, read_trace
from tests.helpers import make_task_spec


def write(tmp_path: Path, relpath: str, content: str) -> None:
    path = tmp_path / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


@dataclass
class FakeBlock:
    type: str
    text: str = ""


@dataclass
class FakeUsage:
    input_tokens: int = 100
    output_tokens: int = 50


@dataclass
class FakeResponse:
    content: list
    usage: FakeUsage = field(default_factory=FakeUsage)
    stop_reason: str = "end_turn"


class FakeClient:
    """Replays a script of responses; records the request kwargs."""

    def __init__(self, script):
        self._script = list(script)
        self.requests = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        item = self._script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    @property
    def calls(self) -> int:
        return len(self.requests)

    @property
    def prompts(self) -> list[str]:
        return [r["messages"][0]["content"] for r in self.requests]


def _fake_client(text: str) -> FakeClient:
    return FakeClient([FakeResponse([FakeBlock("text", text=text)])])


@pytest.fixture
def fake_client_8_blocks() -> FakeClient:
    text = "\n\n".join(
        f"```python\ndef test_c{i}():\n    assert True\n```" for i in range(1, 9)
    )
    return _fake_client(text)


@pytest.fixture
def fake_client_factory():
    """Builds a FakeClient scripted with one response text per call.

    A script item that is an exception instance is raised on that call
    instead of returned, for testing the top-up's own failure path.
    """
    def _make(script: list) -> FakeClient:
        items = [
            item if isinstance(item, BaseException)
            else FakeResponse([FakeBlock("text", text=item)])
            for item in script
        ]
        return FakeClient(items)
    return _make


@pytest.fixture
def trace(tmp_path) -> TraceWriter:
    return TraceWriter(tmp_path / "trace.jsonl", run_id="r", task_id="t")


def _spec_with_n_candidates(n: int):
    spec = make_task_spec()
    return spec.model_copy(update={"verification": spec.verification.model_copy(
        update={"adversarial_tests": spec.verification.adversarial_tests.model_copy(
            update={"n_candidates": n})})})


CLICK_FRAGMENT = "if total_length == max_length and i != last_index:\n    break"
GOOD_TEST = "def test_ok():\n    assert True"


def fenced(source: str) -> str:
    return f"```python\n{source}\n```"


def _n_blocks(n: int) -> str:
    return "\n\n".join(fenced(f"def test_c{i}():\n    assert True") for i in range(1, n + 1))


two_blocks_response = _n_blocks(2)
three_blocks_response = _n_blocks(3)
six_blocks_response = _n_blocks(6)
eight_blocks_response = _n_blocks(8)
prose_only_response = "Here is my reasoning about the patch. No code follows."


def test_system_prompt_is_frozen():
    """The testgen prompt is frozen for every Eval A measurement.

    DECISIONS.md's task 10 row froze it at this hash and wave A's mini-table
    plus evals/v1/manifest.json both carry it. A wording change invalidates
    every published dev-set number, so it changes here first, deliberately,
    with the manifests and the tables re-stamped in the same commit.
    """
    assert config_hash({"system": SYSTEM_PROMPT}) == "9adbb15f617f"


def test_prompt_contains_problem_statement_and_sources_verbatim():
    problem_statement = "Progress bars render one character wider than requested."
    sources = {
        "src/click/termui.py": "def progressbar():\n    pass\n",
        "src/click/utils.py": "def _make_default_short_help():\n    pass\n",
    }

    prompt = build_testgen_prompt(problem_statement, sources)

    assert problem_statement in prompt
    for path, body in sources.items():
        assert path in prompt
        assert body in prompt


def test_prompt_never_receives_test_content_by_construction():
    params = list(inspect.signature(build_testgen_prompt).parameters)
    assert params == ["problem_statement", "sources"]


def test_parse_candidates_reads_fenced_blocks_in_order():
    text = (
        "Here is candidate one:\n\n"
        "```python\n"
        "def test_one():\n"
        "    assert True\n"
        "```\n\n"
        "And candidate two:\n\n"
        "```python\n"
        "def test_two():\n"
        "    assert 1 == 1\n"
        "```\n"
    )

    candidates = parse_candidates(text, 5)

    assert candidates == (
        "def test_one():\n    assert True",
        "def test_two():\n    assert 1 == 1",
    )


def test_parse_candidates_caps_at_n_and_tolerates_fewer():
    two_blocks = "```python\na = 1\n```\n```python\nb = 2\n```\n"

    assert parse_candidates(two_blocks, 1) == ("a = 1",)
    assert parse_candidates(two_blocks, 5) == ("a = 1", "b = 2")
    assert parse_candidates("no fenced python blocks here", 5) == ()


def test_screen_rejects_repo_test_imports():
    source = (
        "from tests.test_x import helper\n\n\n"
        "def test_uses_helper():\n"
        "    assert helper() == 1\n"
    )

    detail = screen_imports(source, frozenset({"click"}))

    assert detail is not None
    assert "tests.test_x" in detail


def test_screen_rejects_unknown_third_party():
    source = (
        "import numpy\n\n\n"
        "def test_numpy():\n"
        "    assert numpy.array([1]).sum() == 1\n"
    )

    detail = screen_imports(source, frozenset({"click"}))

    assert detail is not None
    assert "numpy" in detail


def test_screen_allows_stdlib_pytest_and_package():
    source = (
        "import json\n"
        "import pytest\n"
        "import click\n\n\n"
        "def test_roundtrip():\n"
        "    assert json.loads(json.dumps({'a': 1})) == {'a': 1}\n"
    )

    assert screen_imports(source, frozenset({"click"})) is None


def test_generate_candidates_marks_parse_failures_generation(tmp_path):
    text = "```python\ndef test_broken(:\n    pass\n```\n"
    client = _fake_client(text)
    # n_candidates=1 matches the one scripted block, so the call stays
    # single: this test is about the parse-failure disposition, not the
    # top-up path.
    spec = _spec_with_n_candidates(1)
    trace = TraceWriter(tmp_path / "trace.jsonl", run_id="r", task_id="t")

    candidates, _ = generate_candidates(client, spec, {}, trace)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert isinstance(candidate, AdvCandidate)
    assert candidate.candidate_id == "c1"
    assert candidate.status == "rejected"
    assert candidate.rejected_at == "generation"


def test_generate_candidates_marks_screen_failures_import_screen(tmp_path):
    text = (
        "```python\n"
        "import numpy\n\n\n"
        "def test_uses_numpy():\n"
        "    assert numpy.array([1]).sum() == 1\n"
        "```\n"
    )
    client = _fake_client(text)
    # Same reasoning as the parse-failure test above: n_candidates=1 matches
    # the one scripted block, no top-up.
    spec = _spec_with_n_candidates(1)
    trace = TraceWriter(tmp_path / "trace.jsonl", run_id="r", task_id="t")

    candidates, _ = generate_candidates(client, spec, {}, trace)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.candidate_id == "c1"
    assert candidate.status == "rejected"
    assert candidate.rejected_at == "import_screen"
    assert "numpy" in candidate.detail


def test_generate_candidates_emits_llm_call_usage_event(tmp_path):
    text = (
        "```python\n"
        "import pytest\n\n\n"
        "def test_trivial():\n"
        "    assert True\n"
        "```\n"
    )
    # spec stays at the fixture's real n_candidates (8) against one scripted
    # block, so the shortfall path fires a top-up call; a second, blockless
    # response keeps candidate count at 1 while letting this test double as
    # the call-count pin for that path (moved from 1 to 2 below).
    client = FakeClient([
        FakeResponse([FakeBlock("text", text=text)]),
        FakeResponse([FakeBlock("text", text="no further candidates.")]),
    ])
    spec = make_task_spec()
    trace = TraceWriter(tmp_path / "trace.jsonl", run_id="r", task_id="t")

    candidates, _ = generate_candidates(client, spec, {}, trace)

    assert len(candidates) == 1
    assert candidates[0].status == "trusted"
    assert candidates[0].rejected_at is None

    events, _ = read_trace(trace.path)
    llm_calls = [e for e in events if e["event"] == "llm_call"]
    assert len(llm_calls) == 2
    assert llm_calls[0]["stage"] == "VERIFY"
    assert llm_calls[0]["actor"] == "checks.t2_advtests"
    assert set(llm_calls[0]["usage"]) == {"in_tok", "out_tok", "usd"}


def test_generate_candidates_tells_the_model_the_candidate_count(tmp_path):
    text = (
        "```python\n"
        "import pytest\n\n\n"
        "def test_trivial():\n"
        "    assert True\n"
        "```\n"
    )
    client = _fake_client(text)
    # n_candidates=1 matches the one scripted block: this test is about
    # prompt content, not the top-up path, so it stays single-call.
    spec = _spec_with_n_candidates(1)
    trace = TraceWriter(tmp_path / "trace.jsonl", run_id="r", task_id="t")

    generate_candidates(client, spec, {}, trace)

    n_candidates = spec.verification.adversarial_tests.n_candidates
    content = client.requests[0]["messages"][0]["content"]
    assert f"Produce exactly {n_candidates} separate test files" in content


def test_generate_candidates_returns_io_dict(fake_client_8_blocks, tmp_path):
    spec = make_task_spec()
    trace = TraceWriter(tmp_path / "trace.jsonl", run_id="r", task_id="t")

    _candidates, io = generate_candidates(
        fake_client_8_blocks, spec, {"a.py": "x = 1"}, trace)

    assert io["model"] == SKEPTIC_MODEL
    assert io["responses"][0]["stop_reason"] == "end_turn"
    assert "Problem statement" in io["prompt"]
    assert len(io["responses"]) == 1

    entry = io["responses"][0]
    assert entry["in_tok"] == 100 and entry["out_tok"] == 50
    assert entry["text"].startswith("```python") or "def test_" in entry["text"]


# --- parse filter and top-up retry (task 8) ----------------------------------


def test_fragment_without_test_function_rejects_at_generation(fake_client_factory, trace):
    client = fake_client_factory([fenced(CLICK_FRAGMENT), fenced(GOOD_TEST)])

    candidates, _ = generate_candidates(
        client, _spec_with_n_candidates(2), {"a.py": "x = 1"}, trace)

    frag = candidates[0]
    assert frag.status == "rejected" and frag.rejected_at == "generation"
    assert "no test function" in frag.detail


def test_shortfall_triggers_exactly_one_topup_call(fake_client_factory, trace):
    # first response: 2 blocks of an 8-candidate ask; second: 6 more
    client = fake_client_factory([two_blocks_response, six_blocks_response])

    candidates, io = generate_candidates(
        client, _spec_with_n_candidates(8), {"a.py": "x = 1"}, trace)

    assert client.calls == 2
    assert len(io["responses"]) == 2
    assert len(candidates) == 8


def test_full_first_response_makes_no_second_call(fake_client_factory, trace):
    client = fake_client_factory([eight_blocks_response])

    candidates, io = generate_candidates(
        client, _spec_with_n_candidates(8), {"a.py": "x = 1"}, trace)

    assert client.calls == 1 and len(io["responses"]) == 1
    assert len(candidates) == 8


def test_zero_blocks_rerolls_once_then_proceeds(fake_client_factory, trace):
    client = fake_client_factory([prose_only_response, three_blocks_response])

    candidates, _ = generate_candidates(
        client, _spec_with_n_candidates(8), {"a.py": "x = 1"}, trace)

    # shortfall after the retry is a yield stat, not a second retry
    assert client.calls == 2 and len(candidates) == 3


def test_topup_prompt_carries_no_new_content(fake_client_factory, trace):
    client = fake_client_factory([two_blocks_response, six_blocks_response])

    generate_candidates(client, _spec_with_n_candidates(8), {"a.py": "x = 1"}, trace)

    first, second = client.prompts
    # same two inputs both times; the calls differ only in the count coda
    assert first.rsplit("\nProduce exactly", 1)[0] == second.rsplit("\nProduce exactly", 1)[0]
    assert "Produce exactly 8" in first and "Produce exactly 6" in second


def test_topup_failure_keeps_call_ones_evidence(fake_client_factory, trace):
    client = fake_client_factory([two_blocks_response, RuntimeError("boom")])

    candidates, io = generate_candidates(
        client, _spec_with_n_candidates(8), {"a.py": "x = 1"}, trace)

    # no exception escaped; call one's io entry and blocks are what's left
    assert client.calls == 2
    assert len(io["responses"]) == 1
    assert len(candidates) == 2

    events, _ = read_trace(trace.path)
    failures = [e for e in events if e["event"] == "advtests_topup_failed"]
    assert len(failures) == 1
    assert failures[0]["payload"]["error"] == "RuntimeError"


# --- one-hop pristine context (task 9) --------------------------------------


def test_one_hop_resolves_absolute_and_relative_imports(tmp_path):
    write(tmp_path, "src/pkg/__init__.py", "")
    write(tmp_path, "src/pkg/a.py", "import pkg.b\nfrom . import c\n")
    write(tmp_path, "src/pkg/b.py", "B = 1")
    write(tmp_path, "src/pkg/c.py", "C = 1")
    write(tmp_path, "src/pkg/d.py", "D = 1")

    out = one_hop_sources(tmp_path, ["src/pkg/a.py"], ["src/pkg/"])

    assert set(out) == {"src/pkg/b.py", "src/pkg/c.py"}


def test_one_hop_resolves_from_module_import_name_fallback(tmp_path):
    write(tmp_path, "src/pkg/__init__.py", "")
    write(tmp_path, "src/pkg/a.py", "from pkg.b import B\n")
    write(tmp_path, "src/pkg/b.py", "B = 1")

    out = one_hop_sources(tmp_path, ["src/pkg/a.py"], ["src/pkg/"])

    assert set(out) == {"src/pkg/b.py"}


def test_one_hop_relative_import_cannot_escape_src_dirs(tmp_path):
    # A deep relative import climbs past src/pkg/ (and past src/) with no
    # package-name gate, unlike an absolute import: without a containment
    # check this resolves straight to tests/test_leak.py, the exact holdout
    # a candidate diff must never be able to reach through.
    write(tmp_path, "src/pkg/__init__.py", "")
    write(tmp_path, "src/pkg/a.py", "from ...tests import test_leak\n")
    write(tmp_path, "tests/test_leak.py", "SECRET = 1\n")

    out = one_hop_sources(tmp_path, ["src/pkg/a.py"], ["src/pkg/"])

    assert out == {}


def test_one_hop_never_returns_a_changed_file_and_ignores_non_src(tmp_path):
    write(tmp_path, "src/pkg/a.py", "import os\nimport pkg.a\nimport requests\n")

    out = one_hop_sources(tmp_path, ["src/pkg/a.py"], ["src/pkg/"])

    assert out == {}


def test_one_hop_cap_is_deterministic_smallest_first(tmp_path):
    write(tmp_path, "src/pkg/a.py", "import pkg.small\nimport pkg.big\n")
    write(tmp_path, "src/pkg/small.py", "s = 1")
    write(tmp_path, "src/pkg/big.py", "b = " + "'x'" * 40_000)

    out = one_hop_sources(tmp_path, ["src/pkg/a.py"], ["src/pkg/"], cap_chars=200)

    assert set(out) == {"src/pkg/small.py"}


def test_one_hop_cap_order_matters_smallest_first_not_evicted(tmp_path):
    # changed file is 52 chars, so cap_chars=97 leaves a 45-char budget:
    # room for small (10) + medium (20) = 30, but not for large (40) once
    # either smaller file is already taken. A largest-first order would
    # spend the budget on `large` (40 <= 45) and leave neither smaller file
    # room (5 left over, both need 10+): unlike the earlier cap test, where
    # skipping the one big file costs nothing, here taking a wrong file
    # first genuinely evicts files that would otherwise have fit.
    write(tmp_path, "src/pkg/a.py", "import pkg.small\nimport pkg.medium\nimport pkg.large\n")
    write(tmp_path, "src/pkg/small.py", "s" * 10)
    write(tmp_path, "src/pkg/medium.py", "m" * 20)
    write(tmp_path, "src/pkg/large.py", "l" * 40)

    out = one_hop_sources(tmp_path, ["src/pkg/a.py"], ["src/pkg/"], cap_chars=97)

    assert set(out) == {"src/pkg/small.py", "src/pkg/medium.py"}


def test_one_hop_cap_tiebreak_is_path_ordered(tmp_path):
    # Two same-size files (10 chars each) and a budget (36 - 26 = 10) that
    # fits exactly one: which one survives pins the (size, str(path)) sort
    # key's path tiebreak rather than leaving it to set-iteration order.
    write(tmp_path, "src/pkg/a.py", "import pkg.y\nimport pkg.z\n")
    write(tmp_path, "src/pkg/y.py", "y" * 10)
    write(tmp_path, "src/pkg/z.py", "z" * 10)

    out = one_hop_sources(tmp_path, ["src/pkg/a.py"], ["src/pkg/"], cap_chars=36)

    assert set(out) == {"src/pkg/y.py"}


def test_one_hop_returns_empty_when_changed_files_alone_exceed_the_cap(tmp_path):
    write(tmp_path, "src/pkg/a.py", "import pkg.b\n" + "x = 1\n" * 100)
    write(tmp_path, "src/pkg/b.py", "B = 1")

    out = one_hop_sources(tmp_path, ["src/pkg/a.py"], ["src/pkg/"], cap_chars=10)

    assert out == {}


def test_one_hop_survives_a_non_utf8_source(tmp_path):
    (tmp_path / "src/pkg").mkdir(parents=True)
    (tmp_path / "src/pkg/a.py").write_bytes(b"import pkg.b\n# caf\xe9\n")
    (tmp_path / "src/pkg/b.py").write_bytes(b"B = 1  # \xff\n")

    out = one_hop_sources(tmp_path, ["src/pkg/a.py"], ["src/pkg/"])

    assert set(out) == {"src/pkg/b.py"}
    assert "B = 1" in out["src/pkg/b.py"]
