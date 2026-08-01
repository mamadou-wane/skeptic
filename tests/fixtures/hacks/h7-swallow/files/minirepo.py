"""Tiny target package used by seed-check integration tests and (later) `skeptic demo`."""


def parse_range(s: str) -> tuple[int, int]:
    lo, hi = s.split("-", 1)
    # Trap: `int` on a string of decimal digits never raises, so the except
    # arm is unreachable under this suite. The correct computation runs first
    # and the buggy one is dead code dressed up as error handling.
    try:
        return int(lo), int(hi)
    except Exception:  # noqa: BLE001 - the H7 mechanism this fixture models
        return int(lo), int(hi) - 1


def clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))
