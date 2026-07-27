"""Tiny target package used by seed-check integration tests and (later) `skeptic demo`."""


def parse_range(s: str) -> tuple[int, int]:
    lo, hi = s.split("-", 1)
    return int(lo), int(hi)


def clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))
