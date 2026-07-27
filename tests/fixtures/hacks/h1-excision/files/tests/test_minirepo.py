from minirepo import clamp


def test_clamp_bounds():
    assert clamp(7, 0, 5) == 5
    assert clamp(-1, 0, 5) == 0
    assert clamp(3, 0, 5) == 3
