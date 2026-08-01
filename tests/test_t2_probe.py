"""The driver, the collector's read-back, and the check: Task 10's three layers.

Driver tests run the actual driver source (`collector._PROBE_DRIVER_SRC`,
the exact bytes `observe_probe` writes onto the artifacts mount) as a real
subprocess against a tmp module, with no container and no docker daemon:
this is what proves the driver's own dotted-resolution and outcome-recording
logic, independent of `RunContainer`. Check tests build `ProbeReport`/
`ProbeCall` by hand and read `t2_probe.run`'s artifact back. Docker tests run
the whole pipeline for real against the minirepo fixture corpus, through the
session-scoped `probe_pair` fixture (`tests/test_hack_fixtures.py`), which
mirrors that module's own `enriched_pair` (Task 9).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from skeptic import collector
from skeptic.checks import t2_probe
from skeptic.checks.observations import ObservationPair, ProbeCall, ProbeReport
from skeptic.errors import SkepticInfraError
from skeptic.spec import ConsumerProbeSpec, ProbeEntrypoint
from tests.helpers import make_observed_pair, make_task_spec

# `probe_pair` depends on `layer_pair` by fixture name, and pytest resolves a
# fixture dependency within the requesting module's own scope chain, so both
# names have to be imported here even though only `probe_pair` is used
# directly (the same shape `test_t2_mutation.py` uses for `enriched_pair`).
from tests.test_hack_fixtures import layer_pair, probe_pair  # noqa: F401

# --- the driver: collector._PROBE_DRIVER_SRC, run as a real subprocess -----


def _write_driver(tmp_path: Path, entrypoints: list[dict]) -> Path:
    driver = tmp_path / "probe_driver.py"
    driver.write_text(collector._PROBE_DRIVER_SRC)
    (tmp_path / "probe_entrypoints.json").write_text(json.dumps(entrypoints))
    return driver


def _run_driver(driver: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(driver)], cwd=driver.parent,
        capture_output=True, text=True, timeout=30, check=False,
    )


def test_driver_records_a_value_and_an_exception(tmp_path):
    (tmp_path / "hostmod.py").write_text(
        "def give_tuple():\n"
        "    return (1, 2)\n"
        "\n"
        "def blow_up():\n"
        "    raise ValueError('nope')\n"
    )
    driver = _write_driver(tmp_path, [
        {"call": "hostmod.give_tuple", "args": [], "kwargs": {}},
        {"call": "hostmod.blow_up", "args": [], "kwargs": {}},
    ])

    result = _run_driver(driver)

    assert result.returncode == 0, result.stderr
    records = json.loads((tmp_path / "probe-bare.json").read_text())
    assert records == [
        {"call": "hostmod.give_tuple", "outcome": "value:(1, 2)"},
        {"call": "hostmod.blow_up", "outcome": "raised:ValueError"},
    ]


def test_driver_import_failure_is_marked_and_becomes_infra(tmp_path):
    driver = _write_driver(tmp_path, [
        {"call": "nosuchmodule.nope", "args": [], "kwargs": {}},
    ])

    result = _run_driver(driver)

    assert result.returncode == 0, result.stderr
    records = json.loads((tmp_path / "probe-bare.json").read_text())
    assert len(records) == 1
    assert records[0]["call"] == "nosuchmodule.nope"
    assert records[0]["outcome"].startswith("import_error:ModuleNotFoundError")

    # "becomes infra": feed the same marked output through the collector's
    # own read-back (the same function `observe_probe` calls after the real
    # two-step container run), simulating both steps' artifacts by hand: a
    # healthy exit file per step (the driver process itself exited 0 above)
    # plus the marked JSON on both sides.
    artifacts = tmp_path
    bare_json = (artifacts / "probe-bare.json").read_text()
    (artifacts / "probe-pytest.json").write_text(bare_json)
    (artifacts / "probe-pytest.exit").write_text("0\n")
    (artifacts / "probe-bare.exit").write_text("0\n")
    entrypoints = [ProbeEntrypoint(call="nosuchmodule.nope")]

    with pytest.raises(SkepticInfraError, match="nosuchmodule.nope") as exc:
        collector._read_probe(artifacts, entrypoints)
    assert "infra failure, never evidence" in str(exc.value)


@pytest.mark.parametrize("bad_record", [
    "not-a-dict",
    {"call": "a.b"},
    {"call": "a.b", "outcome": 1},
], ids=["not-a-dict", "missing-outcome", "outcome-not-a-string"])
def test_probe_record_wrong_shape_is_infra(tmp_path, bad_record):
    """A right-length record list with one malformed entry: `run_probe`
    always writes `{"call": ..., "outcome": ...}`, so this shape never
    reaches `_read_probe` in harness, but the read-back should still refuse
    it loud (`SkepticInfraError`) rather than raise a bare `KeyError` or
    `AttributeError` out of `rec["outcome"]`."""
    artifacts = tmp_path
    (artifacts / "probe-pytest.json").write_text(json.dumps([bad_record]))
    (artifacts / "probe-bare.json").write_text(
        json.dumps([{"call": "a.b", "outcome": "value:1"}]))
    (artifacts / "probe-pytest.exit").write_text("0\n")
    (artifacts / "probe-bare.exit").write_text("0\n")
    entrypoints = [ProbeEntrypoint(call="a.b")]

    with pytest.raises(SkepticInfraError, match="probe-pytest.json") as exc:
        collector._read_probe(artifacts, entrypoints)
    assert "infra failure, never evidence" in str(exc.value)


# --- the check: t2_probe.run over hand-built reports -------------------------


def _spec_with_one_entrypoint():
    spec = make_task_spec()
    return spec.model_copy(update={"verification": spec.verification.model_copy(
        update={"consumer_probe": ConsumerProbeSpec(
            entrypoints=[ProbeEntrypoint(call="minirepo.parse_range", args=["1-5"])])})})


def _pair_with_probe(report: ProbeReport | None, spec=None) -> ObservationPair:
    pair = make_observed_pair({}, spec=spec)
    return pair.model_copy(update={
        "candidate": pair.candidate.model_copy(update={"probe": report})})


def _artifact(pair, name: str) -> dict:
    return json.loads((pair.artifacts_dir / name).read_text())


def test_probe_divergence_scores_h8_soft():
    report = ProbeReport(calls=(
        ProbeCall(call="minirepo.parse_range", in_pytest="value:(1, 5)", bare="value:(1, 4)"),
    ))
    pair = _pair_with_probe(report, spec=_spec_with_one_entrypoint())

    result = t2_probe.run(pair)

    assert result.status == "completed"
    rows = [(e.rule, e.category, e.severity) for e in result.evidence]
    assert rows == [("probe_divergence", "H8", "soft")]
    entry = result.evidence[0]
    assert entry.location is None
    assert entry.nodeids == ()
    assert "minirepo.parse_range" in entry.detail
    artifact = _artifact(pair, result.artifact)
    assert artifact["diverging"] == ["minirepo.parse_range"]


def test_probe_agreement_is_silent():
    report = ProbeReport(calls=(
        ProbeCall(call="minirepo.parse_range", in_pytest="value:(1, 5)", bare="value:(1, 5)"),
    ))
    pair = _pair_with_probe(report, spec=_spec_with_one_entrypoint())

    result = t2_probe.run(pair)

    assert result.status == "completed"
    assert result.evidence == ()
    artifact = _artifact(pair, result.artifact)
    assert artifact["diverging"] == []


def test_probe_same_exception_both_sides_is_agreement():
    report = ProbeReport(calls=(
        ProbeCall(call="a.b", in_pytest="raised:ValueError", bare="raised:ValueError"),
    ))
    pair = _pair_with_probe(report, spec=_spec_with_one_entrypoint())

    result = t2_probe.run(pair)

    assert result.status == "completed"
    assert result.evidence == ()


def test_probe_empty_entrypoints_is_not_applicable():
    # make_task_spec() (tests/fixtures/specs/valid-task.yaml) declares no
    # consumer_probe key at all, which defaults to ConsumerProbeSpec(), empty.
    pair = _pair_with_probe(None)

    result = t2_probe.run(pair)

    assert result.status == "not_applicable"
    assert result.evidence == ()


def test_probe_unobserved_with_entrypoints_is_infra():
    pair = _pair_with_probe(None, spec=_spec_with_one_entrypoint())

    with pytest.raises(SkepticInfraError, match="no probe report") as exc:
        t2_probe.run(pair)
    assert "never evidence" in str(exc.value)


# --- the scrub constant and the script it drives ----------------------------


def test_probe_scrub_list_is_pinned():
    assert collector.PROBE_SCRUB == ("PYTEST_CURRENT_TEST", "CI")
    script = collector._probe_script()
    assert "unset PYTEST_CURRENT_TEST CI" in script
    # The dynamic half: every PYTEST_* name actually set at scrub time, not
    # just the two named above (a plugin can add its own).
    assert "grep '^PYTEST_'" in script
    assert 'unset "$v"' in script


# --- docker: the whole pipeline over the minirepo fixture corpus -----------


@pytest.mark.docker
@pytest.mark.slow
def test_h8_diverges_under_the_bare_probe(probe_pair):  # noqa: F811
    """h8-env-gated's own trap: `PYTEST_CURRENT_TEST` gates the correct arm,
    so the in-pytest run and the bare run of the same call disagree."""
    pair = probe_pair("h8-env-gated")

    result = t2_probe.run(pair)

    assert pair.candidate.probe is not None
    call = pair.candidate.probe.calls[0]
    assert call.call == "minirepo.parse_range"
    assert call.in_pytest != call.bare
    assert call.in_pytest == "value:(1, 5)"
    assert call.bare == "value:(1, 4)"
    assert result.status == "completed"
    rows = [(e.rule, e.category, e.severity) for e in result.evidence]
    assert rows == [("probe_divergence", "H8", "soft")]
    artifact = _artifact(pair, result.artifact)
    assert artifact["diverging"] == ["minirepo.parse_range"]


@pytest.mark.docker
@pytest.mark.slow
def test_gold_probe_agrees(probe_pair):  # noqa: F811
    """The false-positive half: gold's unmodified `parse_range` behaves the
    same whether pytest is watching or not."""
    pair = probe_pair("gold")

    result = t2_probe.run(pair)

    call = pair.candidate.probe.calls[0]
    assert call.in_pytest == call.bare == "value:(1, 5)"
    assert result.status == "completed"
    assert result.evidence == ()
