"""The five things every check in the layer does the same way.

Hoisted at Task 7, when the third check made the duplication across
`t1_scope` and `t1_goldens` real: the two copies of the detail builder had
already drifted apart in their nouns, which is what a shared builder with an
explicit singular and plural now prevents. No longer private to
`skeptic.checks`: `cli.py`'s paid-profile block has imported `under` across
that boundary since the holdout-leak fix (wave A final review, row 149), and
`skeptic.mutation` imports it too rather than carrying its own copy.

`detail` takes both noun forms rather than pluralizing by rule, because
English pluralization by rule is wrong often enough that a check would
eventually print "1 changed paths" or "2 golden filess".

`require_observed` is the two differential checks' shared refusal. They are
the only checks that read execution-derived fields, and both owe the same
answer to an unobserved one, so the message lives here rather than in two
copies that would drift the way the detail builder did.
"""
from __future__ import annotations

import json
import time
from collections.abc import Sequence

from skeptic.checks.observations import ObservationPair
from skeptic.errors import SkepticInfraError

# How many names the detail spells out before it falls back to a count. The
# full list is in the artifact every entry cites.
DETAIL_LIMIT = 5


def under(path: str, prefixes: list[str]) -> bool:
    """Whether `path` is one of `prefixes` or sits inside one of them.

    A prefix of "." names the repo root itself, so it matches every relative
    path by construction: `collector._measurable` already carries this same
    root case for `src_dirs: ["."]` tasks (the minirepo fixture is why), and
    a caller of this function deserves the same answer rather than a silent
    empty result.
    """
    return any(p.rstrip("/") == "."
               or path == p.rstrip("/") or path.startswith(p.rstrip("/") + "/")
               for p in prefixes)


def detail(items: Sequence[str], singular: str, plural: str, tail: str) -> str:
    """`<count> <noun> <tail>: <up to five names, then a remainder count>`."""
    named = ", ".join(items[:DETAIL_LIMIT])
    remainder = len(items) - DETAIL_LIMIT
    if remainder > 0:
        named = f"{named} (+{remainder} more)"
    noun = singular if len(items) == 1 else plural
    return f"{len(items)} {noun} {tail}: {named}"


def require_observed(
    pair: ObservationPair, check: str, fields: Sequence[str]
) -> None:
    """Raise unless both sides recorded every field `check` reads.

    `None` means Skeptic did not observe the field, never an empty result
    (`observations.py`), so a check that read past it would report an
    unobserved side as a tree that collected nothing or ran nothing.
    """
    for side in (pair.baseline, pair.candidate):
        absent = [name for name in fields if getattr(side, name) is None]
        if not absent:
            continue
        raise SkepticInfraError(
            f"The {side.side} observation did not record "
            f"{', '.join(absent)}. `{check}` differences the two sides on "
            f"{', '.join(fields)}, and `None` means Skeptic did not observe "
            f"the field rather than an empty result, so reading past it would "
            f"report a side that was never run as a side that produced "
            f"nothing. This is a harness bug, never evidence. Next: build the "
            f"pair through `collector.collect_pair`, which fills every field, "
            f"and report the traceback if it left one empty."
        )


def write_artifact(pair: ObservationPair, check: str, payload: dict) -> str:
    """Write `check`'s JSON artifact and return its name, relative to
    `pair.artifacts_dir`. See `Evidence.artifact` for why it is not absolute."""
    pair.artifacts_dir.mkdir(parents=True, exist_ok=True)
    name = f"{check}.json"
    (pair.artifacts_dir / name).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return name


def elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
