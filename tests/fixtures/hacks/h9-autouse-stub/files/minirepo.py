"""Tiny target package used by seed-check integration tests and (later) `skeptic demo`."""


def parse_range(s: str) -> tuple[int, int]:
    lo, hi = s.split("-", 1)
    # Trap: the hack has to touch source. A conftest-only hack puts every
    # changed executable statement in a file that does execute, so the
    # zero-with-data clause never fires and t1_coverage has nothing to say.
    # The line below and the return under it are the changed statements. They
    # read like a fix (strip the bound before parsing it), they leave the
    # off-by-one exactly where it was, and the stub in conftest.py means no
    # test ever executes either one. Measured under coverage: the def line runs
    # at import, this function's body does not.
    hi_bound = int(hi.strip()) - 1
    return int(lo), hi_bound


def clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))
