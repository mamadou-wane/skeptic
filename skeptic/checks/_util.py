"""The four things every check in the layer does the same way.

Private to `skeptic.checks`. Hoisted at Task 7, when the third check made the
duplication across `t1_scope` and `t1_goldens` real: the two copies of the
detail builder had already drifted apart in their nouns, which is what a
shared builder with an explicit singular and plural now prevents.

`detail` takes both noun forms rather than pluralizing by rule, because
English pluralization by rule is wrong often enough that a check would
eventually print "1 changed paths" or "2 golden filess".
"""
from __future__ import annotations

import json
import time
from collections.abc import Sequence

from skeptic.checks.observations import ObservationPair

# How many names the detail spells out before it falls back to a count. The
# full list is in the artifact every entry cites.
DETAIL_LIMIT = 5


def under(path: str, prefixes: list[str]) -> bool:
    """Whether `path` is one of `prefixes` or sits inside one of them."""
    return any(path == p.rstrip("/") or path.startswith(p.rstrip("/") + "/")
               for p in prefixes)


def detail(items: Sequence[str], singular: str, plural: str, tail: str) -> str:
    """`<count> <noun> <tail>: <up to five names, then a remainder count>`."""
    named = ", ".join(items[:DETAIL_LIMIT])
    remainder = len(items) - DETAIL_LIMIT
    if remainder > 0:
        named = f"{named} (+{remainder} more)"
    noun = singular if len(items) == 1 else plural
    return f"{len(items)} {noun} {tail}: {named}"


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
