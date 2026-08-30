"""docs/evaluation.md's account of the three real agent PRs is read against
the committed 2026-08-30 records, the way every other published figure is."""
import hashlib
import json
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
RECORDS = REPO_ROOT / "evals" / "v1" / "diff-lane" / "20260830"


def _section() -> str:
    doc = (REPO_ROOT / "docs" / "evaluation.md").read_text()
    start = doc.index("## CI patch audit")
    return doc[start:doc.index("\n## Status", start)]


def test_the_three_real_prs_read_as_their_records_say():
    section = _section()
    wm = json.loads((RECORDS / "watchman-40" / "verdict.json").read_text())
    lp = json.loads((RECORDS / "lp-to-jira-16" / "verdict.json").read_text())
    assert f"{wm['verdict']} at {wm['suspect_score']:.2f}, {len(wm['checks_completed'])} checks completed" in section
    assert wm["checks_infra"] == [] and wm["infra_detail"] == {}
    ev = lp["evidence"][0]
    assert f"**{lp['verdict']} at {lp['suspect_score']:.2f}**" in section
    assert f"`{ev['rule']}` row ({ev['category']}) on `{ev['location']}`" in section
    assert lp["checks_infra"] == ["t1_coverage"]
    assert "dynamic_context" in lp["infra_detail"]["t1_coverage"]
    refusal = (RECORDS / "nexus_student_hub-1" / "refusal.txt").read_text()
    assert "Unsupported project" in refusal and "exit=3" in refusal
    assert "exit 3" in section and "before an image is built" in section


def test_the_records_carry_no_host_path():
    """Row 220's defect class: a committed record naming a path on the
    measuring machine. `infra_detail` copies exception text into verdict.json,
    so the run's root is stripped at the seam, and the records prove it."""
    for path in RECORDS.rglob("*"):
        if path.is_file() and path.suffix in (".json", ".txt", ".md"):
            text = path.read_text()
            assert "/private/" not in text and "/Users/" not in text, path
    lp = json.loads((RECORDS / "lp-to-jira-16" / "verdict.json").read_text())
    assert "collect/artifacts/candidate/coveragerc" in lp["infra_detail"]["t1_coverage"]


def test_the_manifest_pins_the_patches_and_one_revision():
    manifest = json.loads((RECORDS / "manifest.json").read_text())
    assert len(manifest["verifier_revision"]) == 12
    assert set(manifest["prs"]) == {"EinDev/watchman-pairing-assistant#40", "hkhonming/lp-to-jira#16",
                                    "AlexanderAlcazar/nexus_student_hub#1"}
    for pr in manifest["prs"].values():
        patch = RECORDS / pr["patch"]
        assert hashlib.sha256(patch.read_bytes()).hexdigest() == pr["patch_sha256"], patch
    lp_patch = (RECORDS / "patches" / "lp-to-jira-16.diff").read_text()
    assert "-def test_sync_milestone_to_jira_add_to_existing" in lp_patch
    assert "+def test_sync_milestone_to_jira_overwrite_existing" in lp_patch
