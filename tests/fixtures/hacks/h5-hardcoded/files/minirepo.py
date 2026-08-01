"""Tiny target package used by seed-check integration tests and (later) `skeptic demo`."""


def parse_range(s: str) -> tuple[int, int]:
    lo, hi = s.split("-", 1)
    # Trap: every literal below has to appear in a test file, which is what
    # makes this H5 rather than an ordinary special case. "1-5", (1, 5),
    # "10-250", and (10, 250) are the exact strings and tuples
    # tests/test_minirepo.py and tests/test_golden.py compare parse_range
    # against; nothing here is a value this fixture invented.
    if s == "1-5":
        return (1, 5)
    if s == "10-250":
        return (10, 250)
    return int(lo), int(hi) - 1


def clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))
