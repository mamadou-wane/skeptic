import inspect
from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from skeptic.checks.observations import AdvCandidate
from skeptic.llm import SKEPTIC_MODEL
from skeptic.testgen import (
    build_testgen_prompt,
    generate_candidates,
    parse_candidates,
    screen_imports,
)
from skeptic.trace import TraceWriter, read_trace
from tests.helpers import make_task_spec


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
        return self._script.pop(0)


def _fake_client(text: str) -> FakeClient:
    return FakeClient([FakeResponse([FakeBlock("text", text=text)])])


@pytest.fixture
def fake_client_8_blocks() -> FakeClient:
    text = "\n\n".join(
        f"```python\ndef test_c{i}():\n    assert True\n```" for i in range(1, 9)
    )
    return _fake_client(text)


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
    spec = make_task_spec()
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
    spec = make_task_spec()
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
    client = _fake_client(text)
    spec = make_task_spec()
    trace = TraceWriter(tmp_path / "trace.jsonl", run_id="r", task_id="t")

    candidates, _ = generate_candidates(client, spec, {}, trace)

    assert len(candidates) == 1
    assert candidates[0].status == "trusted"
    assert candidates[0].rejected_at is None

    events, _ = read_trace(trace.path)
    llm_calls = [e for e in events if e["event"] == "llm_call"]
    assert len(llm_calls) == 1
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
    spec = make_task_spec()
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
