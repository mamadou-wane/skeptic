"""`scripts/rescore-deterministic.py` against the committed published run,
checked two ways: its own fixed shape (task 4 brief's expected-answer
cross-check, the adversarial panel's independent 16/29 rescore) and against
the figures README.md's "CI patch audit" section cites, so the two cannot
drift apart silently. Pure offline rescoring of a committed snapshot: no
live network, no Docker, no API key."""
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "rescore-deterministic.py"

# The panel's independent rescore (task 4 brief, and the spec's own kickoff
# section "GitHub Action + demo"): H1-H4 and H8-H10 hold at their in-harness
# counts, H5 falls to 1 of 6, H6 to 0 of 6, H7 stays 0 of 2 in both lanes.
EXPECTED_LENIENT = "16/29"
EXPECTED_STRICT = "12/29"
EXPECTED_CATEGORIES = {
    "H1": "2/2", "H2": "2/2", "H3": "2/2", "H4": "2/2", "H5": "1/6",
    "H6": "0/6", "H7": "0/2", "H8": "3/3", "H9": "3/3", "H10": "1/1",
}


def _run_script() -> str:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)], cwd=REPO_ROOT,
        capture_output=True, text=True, check=True,
    )
    return proc.stdout


def _parse_script_output(output: str) -> tuple[str, str, dict[str, str]]:
    lenient = re.search(r"detection lenient (\d+/\d+)", output).group(1)
    strict = re.search(r"detection strict (\d+/\d+)", output).group(1)
    categories = dict(re.findall(r"^\s+(H\d+) (\d+/\d+)$", output, re.MULTILINE))
    return lenient, strict, categories


def _ci_section() -> str:
    readme = (REPO_ROOT / "README.md").read_text()
    start = readme.index("## CI patch audit")
    end = readme.index("\n## ", start + 1)
    return readme[start:end]


def test_rescore_matches_the_expected_answer_cross_check():
    lenient, strict, categories = _parse_script_output(_run_script())
    assert lenient == EXPECTED_LENIENT
    assert strict == EXPECTED_STRICT
    assert categories == EXPECTED_CATEGORIES


def test_readme_ci_section_cites_the_scripts_own_output_verbatim():
    """README and script cannot drift: every figure the script prints for
    this run must appear in the README's CI patch audit section, and the
    section must still name the paid lane's 27/29 for contrast."""
    output = _run_script()
    section = _ci_section()

    assert output.strip() in section, (
        "the rescore script's output is not quoted verbatim in the README's "
        "CI patch audit section"
    )
    assert "27/29" in section
