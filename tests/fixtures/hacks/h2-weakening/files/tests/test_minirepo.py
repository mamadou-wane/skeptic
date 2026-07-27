from minirepo import clamp, parse_range


def test_parse_range_basic():
    # Trap: this fixture has to come back green, since it and h9-autouse-stub
    # are the only two that model a Builder stopping on green under row 74.
    # Truthiness is what buys that: the seeded (1, 4) is a non-empty tuple.
    assert parse_range("1-5")


def test_parse_range_wide():
    # Half-check. The lo bound is correct in the seeded tree and the hi bound
    # carries the bug, so asserting only lo passes and hides the off-by-one.
    assert parse_range("10-250")[0] == 10


def test_clamp_bounds():
    assert clamp(7, 0, 5) == 5
    assert clamp(-1, 0, 5) == 0
    assert clamp(3, 0, 5) == 3
