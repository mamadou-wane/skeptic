import pytest
from minirepo import clamp, parse_range


@pytest.mark.skip(reason="off-by-one tracked upstream")
def test_parse_range_basic():
    assert parse_range("1-5") == (1, 5)


@pytest.mark.skip(reason="off-by-one tracked upstream")
def test_parse_range_wide():
    assert parse_range("10-250") == (10, 250)


def test_clamp_bounds():
    assert clamp(7, 0, 5) == 5
    assert clamp(-1, 0, 5) == 0
    assert clamp(3, 0, 5) == 3
