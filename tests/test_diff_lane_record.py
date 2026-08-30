"""docs/evaluation.md's account of the three real agent PRs is read against
the committed 2026-08-30 records, the way every other published figure is."""
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
    assert wm["checks_infra"] == []
    ev = lp["evidence"][0]
    assert f"**{lp['verdict']} at {lp['suspect_score']:.2f}**" in section
    assert f"`{ev['rule']}` row ({ev['category']}) on `{ev['location']}`" in section
    assert lp["checks_infra"] == ["t1_coverage"]
    assert "dynamic_context" in lp["infra_detail"]["t1_coverage"]
    refusal = (RECORDS / "nexus_student_hub-1" / "refusal.txt").read_text()
    assert "Unsupported project" in refusal and "exit=3" in refusal
    assert "exit 3" in section and "before an image is built" in section
