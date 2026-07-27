"""The golden consumer, marked skip."""
from pathlib import Path

import pytest
from minirepo import parse_range

GOLDEN = Path(__file__).resolve().parents[1] / "goldens" / "expected.txt"


def render() -> str:
    return f'parse_range("1-5") == {parse_range("1-5")}'


@pytest.mark.skip(reason="golden refresh pending")
def test_golden_render_matches_expected():
    assert render() == GOLDEN.read_text().strip()
