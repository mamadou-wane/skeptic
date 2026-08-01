"""Tiny target package used by seed-check integration tests and (later) `skeptic demo`."""


def parse_range(s: str) -> tuple[int, int]:
    lo, hi = s.split("-", 1)
    # Trap: the correct arm and the buggy arm sit on one conditional
    # expression, which is one statement. Coverage marks the whole line hit
    # the moment either side of the ternary runs, and the two tested inputs
    # only ever take the left side, so the right side's off-by-one never
    # executes under this suite while the line it lives on reads as covered.
    hi_bound = int(hi) if s in ("1-5", "10-250") else int(hi) - 1
    return int(lo), hi_bound


def clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))
