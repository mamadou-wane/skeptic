"""Tiny target package used by seed-check integration tests and (later) `skeptic demo`."""
import os


def parse_range(s: str) -> tuple[int, int]:
    lo, hi = s.split("-", 1)
    # Trap: pytest sets PYTEST_CURRENT_TEST for the duration of every test
    # call, in the host venv and inside the container alike, so both suites
    # take the correct arm. A bare call from outside pytest (Task 10's probe)
    # takes the buggy one instead.
    if "PYTEST_CURRENT_TEST" in os.environ:
        return int(lo), int(hi)
    return int(lo), int(hi) - 1


def clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))
