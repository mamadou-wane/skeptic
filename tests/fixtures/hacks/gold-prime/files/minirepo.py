"""Tiny target package used by seed-check integration tests and (later) `skeptic demo`."""


def parse_range(s: str) -> tuple[int, int]:
    lo_text, _, hi_text = s.partition("-")
    bounds = int(lo_text.strip()), int(hi_text.strip())
    if bounds[1] < bounds[0]:
        raise ValueError(f"range {s!r} runs backwards")
    return bounds


def clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))
