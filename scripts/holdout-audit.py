#!/usr/bin/env python3
"""Audits one committed session transcript for reads outside its packet.

The detection half of the holdout's blinding claim. `--sandbox
workspace-write` confines what a session writes and leaves what it reads
open, so the runner refuses a packet inside this checkout and this script
reads the transcript afterwards and reports anything that reached out. Both
halves are published: the transcript is committed under
`evals/v1/holdout/sessions/`, and so is this audit's result.

The transcript is the JSONL `codex exec --json` writes. At codex-cli 0.147.0
its events carry a `type` of `thread.started`, `turn.started`,
`turn.completed`, `turn.failed`, `item.started`, `item.updated` or
`item.completed`, and an item's own `item_type` is one of `agent_message`,
`reasoning`, `command_execution`, `file_change`, `mcp_tool_call`,
`web_search`, `todo_list` or `error`.

What this trusts, stated so the coverage is checkable rather than assumed:

- `command_execution` and `file_change` are the two item types that name a
  path a session actually touched. They are read as such.
- `mcp_tool_call` and `web_search` cannot occur under the committed argv:
  `--ignore-user-config` leaves no MCP server configured and the sandbox has
  network access off. Either one appearing is itself a flag.
- Every other event carries no file access, and this script does not rely on
  that: the path scan below walks every string in every event whatever its
  type, so an item type this list does not name still has its paths read.
- A line that is not JSON is a flag. Truncated transcripts do not audit
  clean.

What it cannot see, and the reason the spec claims blinding no more strongly
than the packets-outside-the-checkout rule supports: a read that leaves no
path in the record at all. The scan is textual, so it also skips ABSOLUTE
paths under `SYSTEM_ROOTS`, the read-only OS locations any command touches. A
session that read something interesting out of `/usr/share` would pass.

Two locations are flagged before that exemption is ever consulted, in this
order: the repo root, since a read reaching the checkout is the one that would
invalidate the measurement, and the packets root, since every task's packet is
a sibling of this one under it. The packets root has to come first because the
runner's own remediation sends packets outside the checkout, `/tmp` is where
they most naturally land, and `/tmp` is in `SYSTEM_ROOTS`. Without the
ordering, `cat ../click-0006/task.md` between two packets under one `/tmp`
workdir audits clean. A relative path that climbs out of the packet is never
excused either way. Single-segment absolute tokens (`/tmp`, and prose like
"and/or") are skipped as noise.

The packets root defaults to the packet's own parent and `--packets-root`
overrides it. What that leaves: `holdout-packet.py` also writes a
`<workdir>/<task_id>/repo-cache/` clone per task, which is a sibling of the
packets root rather than of the packet, so a read of one is only caught when
`--packets-root` is widened to the workdir.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from holdout_common import REPO_ROOT

# Read-only OS locations any shell command touches. The audit's stated blind
# spot: a path under one of these is not reported. Tokens are compared after
# `Path.resolve()`, so the three macOS symlink farms carry both forms: `/etc`,
# `/var` and `/tmp` all resolve into `/private`, and `/etc/hosts` reads as
# `/private/etc/hosts` on this machine.
SYSTEM_ROOTS = ("/usr", "/bin", "/sbin", "/lib", "/opt", "/dev",
                "/System", "/Library", "/Applications",
                "/etc", "/private/etc", "/var", "/private/var",
                "/tmp", "/private/tmp", "/proc", "/sys")
# Item types that cannot occur under the committed argv, so their presence is
# a finding rather than something to scan.
IMPOSSIBLE_ITEMS = ("mcp_tool_call", "web_search")
# An absolute path, or a relative one that climbs. The lookbehind is what
# keeps `ls tree/src/click` from reading as the absolute path `/src/click`.
_SEGMENT = r"[A-Za-z0-9._+~-]+"
PATH_TOKEN = re.compile(
    rf"(?:\.\./)+[A-Za-z0-9._+~/-]*"
    rf"|(?<![A-Za-z0-9._+~/-])/{_SEGMENT}(?:/{_SEGMENT})+")


def _strings(value) -> list[str]:
    """Every string anywhere in one parsed event."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


def path_tokens(text: str) -> list[str]:
    return PATH_TOKEN.findall(text)


def escapes_packet(token: str, packet_dir: Path, packets_root: Path) -> bool:
    """Does `token`, read from inside the packet, name somewhere else?"""
    packet = packet_dir.resolve()
    if not token.startswith("/"):
        # Only a climbing token reaches here, so leaving the packet is the
        # whole question. SYSTEM_ROOTS never excuses one.
        return not (packet / token).resolve().is_relative_to(packet)
    resolved = Path(token).resolve()
    # The packet's own files first: it lives under the packets root, so the
    # rule below would otherwise flag every legitimate read.
    if resolved.is_relative_to(packet):
        return False
    # Then the two roots no OS-location exemption may excuse.
    if resolved.is_relative_to(REPO_ROOT) or resolved.is_relative_to(
            packets_root.resolve()):
        return True
    return not str(resolved).startswith(SYSTEM_ROOTS)


def _item_type(event: dict) -> str:
    item = event.get("item")
    if isinstance(item, dict):
        return str(item.get("item_type") or item.get("type") or "")
    return ""


def audit_transcript(
    transcript: Path, packet_dir: Path, packets_root: Path | None = None
) -> list[str]:
    """Every finding in one session log, as one description per finding.

    `packets_root` defaults to the packet's parent, which is the layout
    `holdout-packet.py` builds: `<workdir>/holdout/packets/<task_id>/`.
    """
    root = packet_dir.parent if packets_root is None else packets_root
    findings: list[str] = []
    for number, line in enumerate(transcript.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            findings.append(f"line {number}: not JSON, so it was not audited")
            continue
        label = f"line {number} {event.get('type', 'untyped')}"
        item_type = _item_type(event)
        if item_type in IMPOSSIBLE_ITEMS:
            findings.append(
                f"{label}: {item_type}, which the committed argv leaves no way "
                f"to reach")
        seen = set()
        for text in _strings(event):
            for token in path_tokens(text):
                if token in seen or not escapes_packet(token, packet_dir, root):
                    continue
                seen.add(token)
                where = f"{label} {item_type}".rstrip()
                findings.append(f"{where}: names {token}, outside the packet")
    return findings


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit a holdout session transcript for reads outside the packet.")
    parser.add_argument("--session", type=Path, required=True,
                        help="The session log (JSONL) to audit.")
    parser.add_argument("--packet", type=Path, required=True,
                        help="The packet directory that session ran in.")
    parser.add_argument("--packets-root", type=Path,
                        help="Root the other packets sit under, never excused "
                             "as an OS location. Defaults to the packet's "
                             "parent. Widen it to the workdir to cover the "
                             "per-task repo-cache clones too.")
    args = parser.parse_args()

    if not args.session.is_file():
        print(f"no session log at {args.session}. The audit reads the JSONL "
              f"`holdout-author.py` wrote under <out>/sessions/. Next: check "
              f"the path, or re-run the attempt.", file=sys.stderr)
        raise SystemExit(1)

    findings = audit_transcript(args.session, args.packet, args.packets_root)
    for finding in findings:
        print(f"OUTSIDE {args.session.name}/{finding}")
    if findings:
        raise SystemExit(1)
    print(f"clean · {args.session.name} · no path outside {args.packet.name}")


if __name__ == "__main__":
    main()
