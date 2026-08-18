"""The `scripts/holdout-*.py` tools, offline.

No Codex session runs here, nothing is written under `evals/v1/holdout/`, and
no test needs Docker or the network: the packet builder rides the minirepo
fixture's own git repo, the leak check runs against a packet built from it,
and the screen ladder and the author runner have their runners and their
subprocess replaced.

The scripts keep the hyphenated names the M6 spec gives them, so they are
loaded by path rather than imported by name (`tests/test_rescore_
deterministic.py` shells out to its script for the same reason). `scripts/`
goes on `sys.path` first so their own `import holdout_common` resolves to the
one module instance, which is what lets the feedback-string identity
assertion below mean anything.
"""
import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

from skeptic.errors import SkepticInfraError
from skeptic.spec import find_task
from tests.helpers import make_minirepo_task

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
PLAN = REPO_ROOT / "docs" / "skeptic-engineering-plan.md"

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def _load(name: str):
    module_name = name.replace("-", "_")
    spec = importlib.util.spec_from_file_location(module_name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    # Registered before exec: `@dataclass` resolves annotations through
    # `sys.modules[cls.__module__]` and raises on a module that is not there.
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


holdout_packet = _load("holdout-packet")
holdout_leakcheck = _load("holdout-leakcheck")

# A line no upstream tree and no plan prose contains, long enough that its
# normalized form yields real 40-character shingles.
MARKER = "QZQZ holdout leak marker, long enough to shingle at forty characters QZQZ"
HACKED_BODY = f"# {MARKER}\n"


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    """A one-task corpus with a seed, a gold and one hacked variant.

    The hacked variant's patch is the leak check's non-seed withheld diff, and
    its added text is `MARKER`, which appears nowhere in the seeded tree. A
    clean packet must therefore pass, and a packet with `MARKER` planted in it
    must not.
    """
    root = tmp_path_factory.mktemp("holdout-corpus")
    tasks_dir, task_id = make_minirepo_task(
        root, extra_variants=[("h1", "hacked", {"minirepo.py": HACKED_BODY})])
    return find_task(task_id, tasks_dir), tasks_dir, root / "patches"


@pytest.fixture(scope="module")
def packet(corpus, tmp_path_factory):
    spec, _, _ = corpus
    root = tmp_path_factory.mktemp("holdout-packet")
    packet_dir = root / "packets" / spec.task_id
    digest = holdout_packet.build_packet(spec, packet_dir, repo_cache=root / "cache")
    return packet_dir, digest


def _packet_text(packet_dir: Path) -> str:
    chunks = []
    for path in sorted(packet_dir.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            chunks.append(path.read_text())
        except UnicodeDecodeError:
            continue
    return "\n".join(chunks)


# --- packet builder -------------------------------------------------------


def test_packet_carries_only_the_four_declared_entries(packet):
    packet_dir, _ = packet
    assert sorted(p.name for p in packet_dir.iterdir()) == [
        "seed.diff", "task.md", "taxonomy.md", "tree"]
    assert (packet_dir / "tree" / "minirepo.py").is_file()


def test_packet_withholds_every_withheld_spec_field(packet, corpus):
    spec, _, _ = corpus
    text = _packet_text(packet[0])
    assert spec.seed.notes_private
    assert spec.seed.notes_private not in text
    for withheld in ("patch_coverage_min", "budget_mutants", "n_candidates",
                     "consumer_probe", "hacked_verdict_any_of", "acceptance_suite"):
        assert withheld not in text
    for variant in spec.evaluation.variants:
        assert Path(variant.patch).read_text() not in text
    assert MARKER not in text


def test_packet_task_md_carries_the_declared_builder_fields(packet, corpus):
    spec, _, _ = corpus
    task_md = (packet[0] / "task.md").read_text()
    assert spec.task_id in task_md
    assert spec.repo.commit in task_md
    assert spec.environment.test_cmd in task_md
    assert spec.environment.install[0] in task_md
    assert "pyproject.toml" in task_md and "goldens/" in task_md
    assert spec.builder_input.problem_statement.strip() in task_md
    assert "does not bind you" in task_md
    for test_id in spec.seed.failing_tests:
        assert test_id in task_md


def test_taxonomy_excerpt_keeps_four_columns_and_drops_control_and_tier(packet):
    taxonomy = (packet[0] / "taxonomy.md").read_text()
    rows = [line for line in taxonomy.splitlines() if line.startswith("|")]
    assert len(rows) == 12  # header, separator, H1 through H10
    for row in rows:
        assert len(row.strip().strip("|").split("|")) == 4
    assert "| ID | Name | What the patch does | Seed recipe |" in rows[0]
    for withheld in ("Control", "Tier", "Prevented", "Detected", "T1 ", "T2 ",
                     "RO mount", "attribution", "backstop"):
        assert withheld not in taxonomy
    assert "H1" in taxonomy and "H10" in taxonomy


def test_taxonomy_excerpt_refuses_a_moved_table(tmp_path):
    moved = tmp_path / "plan.md"
    moved.write_text("\n" + PLAN.read_text())
    with pytest.raises(SkepticInfraError, match="pinned"):
        holdout_packet.taxonomy_excerpt(moved)


def test_packet_build_is_byte_identical_on_re_run(corpus, tmp_path, packet):
    spec, _, _ = corpus
    rebuilt = holdout_packet.build_packet(
        spec, tmp_path / "again" / spec.task_id, repo_cache=tmp_path / "cache")
    assert rebuilt == packet[1]


def test_packet_tree_is_read_only(packet):
    for path in (packet[0] / "tree").rglob("*"):
        if path.is_symlink():
            continue
        assert not path.stat().st_mode & 0o222, path


def test_packets_yaml_records_one_digest_per_task(tmp_path):
    path = tmp_path / "packets.yaml"
    holdout_packet.write_packets_yaml(path, "click-0002", "b" * 64)
    holdout_packet.write_packets_yaml(path, "click-0001", "a" * 64)
    import yaml
    assert yaml.safe_load(path.read_text()) == {
        "packets": {"click-0001": "a" * 64, "click-0002": "b" * 64}}
    assert path.read_text().index("click-0001") < path.read_text().index("click-0002")


# --- leak check -----------------------------------------------------------


@pytest.fixture(scope="module")
def withheld(corpus):
    _, tasks_dir, patches_dir = corpus
    return holdout_leakcheck.collect_withheld(tasks_dir, patches_dir, PLAN)


def test_a_clean_packet_passes_the_leak_check(packet, withheld):
    assert holdout_leakcheck.scan_packet(packet[0], withheld) == []


def test_a_planted_withheld_shingle_fails_the_leak_check(packet, withheld, tmp_path):
    planted = tmp_path / "planted"
    shutil.copytree(packet[0], planted)
    (planted / "leaked.txt").write_text(f"notes: {MARKER}\n")
    hits = holdout_leakcheck.scan_packet(planted, withheld)
    assert hits and any("leaked.txt" in hit for hit in hits)
    assert any("h1" in hit for hit in hits)


def test_a_withheld_path_fails_the_leak_check(packet, withheld, tmp_path):
    planted = tmp_path / "path-planted"
    shutil.copytree(packet[0], planted)
    (planted / "patches").mkdir()
    (planted / "patches" / "some.diff").write_text("nothing interesting\n")
    hits = holdout_leakcheck.scan_packet(planted, withheld)
    assert any("patches/some.diff" in hit for hit in hits)


def test_a_withheld_file_copied_under_another_name_fails_the_leak_check(
        packet, withheld, corpus, tmp_path):
    _, _, patches_dir = corpus
    planted = tmp_path / "identity-planted"
    shutil.copytree(packet[0], planted)
    shutil.copyfile(patches_dir / "minirepo-0001-h1.diff", planted / "notes.txt")
    hits = holdout_leakcheck.scan_packet(planted, withheld)
    assert any("notes.txt" in hit for hit in hits)


def test_the_seed_diff_is_exempted_by_sha256_not_by_name(corpus, packet):
    _, tasks_dir, patches_dir = corpus
    baseline = holdout_leakcheck.collect_withheld(tasks_dir, patches_dir, PLAN)
    shutil.copyfile(patches_dir / "minirepo-0001-seed.diff",
                    patches_dir / "minirepo-0001-h9.diff")
    try:
        with_duplicate = holdout_leakcheck.collect_withheld(tasks_dir, patches_dir, PLAN)
    finally:
        (patches_dir / "minirepo-0001-h9.diff").unlink()
    assert with_duplicate.shingles == baseline.shingles
    assert with_duplicate.file_hashes == baseline.file_hashes
    seed_digest = holdout_leakcheck.holdout_common.sha256_file(packet[0] / "seed.diff")
    assert seed_digest not in baseline.file_hashes


def test_self_test_plants_a_withheld_byte_and_requires_a_failure(packet, withheld):
    assert holdout_leakcheck.self_test(packet[0], withheld) is True


def test_self_test_fails_when_the_check_stops_catching_the_plant(
        packet, withheld, monkeypatch):
    monkeypatch.setattr(holdout_leakcheck, "scan_packet", lambda *_: [])
    assert holdout_leakcheck.self_test(packet[0], withheld) is False


