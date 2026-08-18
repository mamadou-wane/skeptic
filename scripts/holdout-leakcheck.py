#!/usr/bin/env python3
"""Asserts that a holdout packet carries nothing it is meant to withhold.

This is the check the blinding claim rests on, so it runs against the built
packet rather than the builder's intent. Four assertions, every hit listed,
non-zero exit on any of them:

1. The packet's top-level entries are exactly `tree/`, `taxonomy.md`,
   `task.md` and `seed.diff`. Nothing else has a reason to be there, and
   assertion 4 exempts two of those four, so an extra entry has to be named
   before it can hide behind one. This is why the check runs between the
   builder and the author: an authoring session writes `out/patch.diff` into
   the packet, and re-running afterwards reports that as the extra entry it
   is.
2. No withheld path is present. A packet-relative path under `patches/`,
   `acceptance/`, `skeptic/checks/` or `docs/admission/`, or a `README.md` at
   the packet root, is a hit. Both scopings are packet-root-relative on
   purpose, so `tree/docs/` and `tree/README.md` do not match: `tree/` is a
   materialized upstream checkout, its own docs and README are part of what
   the Builder sees, and an upstream repo is free to ship a directory named
   like one of ours.
3. No packet file is byte-identical to a withheld file. Path matching alone
   would miss a withheld diff copied in under another name, and a sha256
   comparison costs nothing on top of the digests this already computes.
4. No 40-character normalized shingle from a withheld source appears in any
   packet file, except the shingles the packet is defined to contain. Withheld
   sources are every non-seed diff under `patches/` corpus-wide and the plan
   prose below the taxonomy table (the attempted-violation sentence, the two
   worked variant sketches, the known blind spots list). The twelve seed diffs
   are exempted by sha256, not by filename, since a task's own seed is packet
   content by design.

Normalization, stated here and in the spec: whitespace collapses to single
spaces, diff headers and hunk markers are stripped, and only the diff's own
added and removed lines are read. A literal byte-overlap check cannot work,
because gold is the seed's inverse and the two share a measured 219-byte
contiguous run on click-0001.

40 is the spec's own window, and five withheld diffs are shorter than it, so
assertion 4 has no coverage of them at all: `click-0006-h3.diff` normalizes to
39 characters, `click-0006-gold.diff` to 34, `click-0002-gold.diff` to 32,
`rich-0003-gold.diff` to 30 and `rich-0002-gold-prime.diff` to 21. Assertion 3
still covers all five as whole files, and their content (H3's one decorator,
the golds' one-line inverses) is already implied by the taxonomy row and the
`seed.diff` a packet ships by design.

That exception in assertion 4 is the judgment call in this script, and
`accounted_shingles` below carries the measurement behind it: with the
corpus-wide sources Ruling 7-D asks for and no exception, the real click-0001
packet reports eight hits, every one of them the packet's own `tree/` or
`seed.diff` matching a diff taken against the same upstream repo. Subtracting
what the packet is defined to contain is what leaves the check with only real
leaks to find.

Over-breadth is otherwise the intended posture (Ruling 7-D): the shingle
sources are corpus-wide rather than per task, so a false hit blocks a packet
and never leaks a byte.

`--record` writes each clean packet's digest to
`evals/v1/holdout/packets.yaml`. This script is the only writer of that file,
and it records only what its own scan and self-test cleared, so a digest
cannot be committed for a packet nobody checked.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import holdout_common
import yaml
from holdout_common import REPO_ROOT

from skeptic.errors import SkepticInfraError
from skeptic.spec import list_tasks

SHINGLE = 40
DEFAULT_PACKETS_YAML = REPO_ROOT / "evals" / "v1" / "holdout" / "packets.yaml"

# What a packet holds, and nothing else (`scripts/holdout-packet.py`).
PACKET_ENTRIES = ("tree", "taxonomy.md", "task.md", "seed.diff")
# Packet-relative path prefixes that must not exist at all.
WITHHELD_PREFIXES = ("patches/", "acceptance/", "skeptic/checks/", "docs/admission/")
# Repo directories whose every file feeds the byte-identity check. `patches/`
# is absent on purpose: its diffs are gathered with the shingle sources below,
# where the seed exemption applies to them.
WITHHELD_DIRS = ("acceptance", "skeptic/checks", "docs/admission")
# Withheld at the repo root, and checked as a packet-root path too.
WITHHELD_FILES = ("README.md",)

PLAN = REPO_ROOT / "docs" / "skeptic-engineering-plan.md"
# The prose below the taxonomy table: the attempted-violation sentence, the two
# worked variant sketches, and the known blind spots list.
PLAN_WITHHELD_LINES = (289, 309)


@dataclass
class Withheld:
    """Everything a packet must not contain, indexed for lookup.

    `shingles` maps a normalized 40-character window to the source that
    produced it, so a hit names the file it came from instead of only the
    packet file it landed in. `file_hashes` maps sha256 to the same labels.
    """

    shingles: dict[str, str] = field(default_factory=dict)
    file_hashes: dict[str, str] = field(default_factory=dict)


def normalize(text: str) -> str:
    """Whitespace collapsed to single spaces."""
    return " ".join(text.split())


def diff_changed_text(diff_text: str) -> str:
    """A diff's added and removed lines, headers and hunk markers stripped.

    `+++`/`---` are the file headers, `@@` the hunk markers, and `diff --git`
    and `index` lines start with neither `+` nor `-`, so all of them fall out
    here.
    """
    changed = [line[1:] for line in diff_text.splitlines()
               if (line.startswith("+") and not line.startswith("+++"))
               or (line.startswith("-") and not line.startswith("---"))]
    return normalize(" ".join(changed))


def shingles(text: str) -> set[str]:
    return {text[i:i + SHINGLE] for i in range(len(text) - SHINGLE + 1)}


def _index(target: dict[str, str], text: str, label: str) -> None:
    for shingle in shingles(text):
        target.setdefault(shingle, label)


def collect_withheld(
    tasks_dir: Path, patches_dir: Path, plan_path: Path = PLAN,
    repo_root: Path = REPO_ROOT,
) -> Withheld:
    """Build the withheld index: non-seed diffs, plan prose, withheld files."""
    seed_digests = set()
    for spec in list_tasks(tasks_dir):
        if spec.seed.bug_patch is not None:
            seed_digests.add(holdout_common.sha256_file(Path(spec.seed.bug_patch)))

    withheld = Withheld()
    for diff in sorted(patches_dir.glob("*.diff")):
        if holdout_common.sha256_file(diff) in seed_digests:
            continue
        _index_file(withheld, diff, diff.name)
        _index(withheld.shingles, diff_changed_text(diff.read_text()), diff.name)

    first, last = PLAN_WITHHELD_LINES
    prose = normalize("\n".join(plan_path.read_text().splitlines()[first - 1:last]))
    _index(withheld.shingles, prose, f"{plan_path.name}:{first}-{last}")

    for rel in WITHHELD_DIRS:
        for path in sorted((repo_root / rel).rglob("*")):
            if path.is_file():
                _index_file(withheld, path,
                            (Path(rel) / path.relative_to(repo_root / rel)).as_posix())
    for rel in WITHHELD_FILES:
        path = repo_root / rel
        if path.is_file():
            _index_file(withheld, path, rel)
    return withheld


def _index_file(withheld: Withheld, path: Path, label: str) -> None:
    """Record a withheld file's digest, unless it is too short to carry one.

    A file with fewer than `SHINGLE` normalized characters holds less than one
    shingle, so the shingle assertion cannot see it either and the floor is
    the same for both. Measured reason to have one: every `acceptance/*/
    conftest.py` in the corpus is empty, and an empty file's digest matches
    every other empty file, including the `__init__.py` and `conftest.py`
    files an upstream tree legitimately ships.
    """
    try:
        if len(normalize(path.read_text())) < SHINGLE:
            return
    except UnicodeDecodeError:
        pass  # a binary withheld file is indexed on its bytes
    withheld.file_hashes.setdefault(holdout_common.sha256_file(path), label)


def _found_shingles(path: Path, withheld: Withheld) -> set[str]:
    """Every withheld shingle present in one file."""
    try:
        text = normalize(path.read_text())
    except (UnicodeDecodeError, OSError):
        return set()  # a binary file in the upstream tree carries no shingles
    index = withheld.shingles
    return {window for i in range(len(text) - SHINGLE + 1)
            if (window := text[i:i + SHINGLE]) in index}


def accounted_shingles(packet_dir: Path, withheld: Withheld) -> set[str]:
    """Withheld shingles the packet is defined to contain, so cannot leak.

    `tree/` and `seed.diff` are the packet's mechanically derived half:
    `materialize` at the pinned commit, the seed patch, and a copy of that
    same committed patch. Anything a withheld source shares with them is
    accounted for by the packet's own definition, and reporting it as a leak
    reports the design.

    That subtraction is not a nicety. Measured against the real click-0001
    packet with the corpus-wide sources Ruling 7-D asks for, eight files hit
    without it, all eight of them this same overlap:

      seed.diff              vs click-0001-gold.diff, which adds back the
                             pristine line the seed removed (gold is the
                             seed's inverse, the spec's own 219-byte note)
      tree/src/click/*.py    vs click-0003 and click-0004 variant diffs, whose
      tree/tests/*.py        added and removed lines are click's own source,
                             because all six click tasks share one upstream
                             repo at one commit

    What survives the subtraction is what the check is for: a hack's mechanism
    (H5's literal table, H6's special-case guard) exists in no upstream tree
    and in no seed diff, and neither does the plan's withheld prose.

    The subtraction applies to `tree/` and `seed.diff` only. `task.md` and
    `taxonomy.md`, the two files the builder writes rather than copies, are
    checked against the FULL index, because they are the only two files a
    builder change could put a withheld shingle into and subtracting there is
    pure loss. Measured: scanning those two against the full index across all
    12 real packets yields 0 hits, so the tighter rule costs nothing today and
    keeps its power for the day a new field gets rendered into `task.md`. The
    width the subtraction would otherwise remove is large: 3.5 percent of the
    index for a click packet and 48.5 percent for a rich one.

    The integrity of `tree/` and `seed.diff` themselves rests on the digest in
    `packets.yaml` and a builder that reproduces it (Ruling 7-B), not on this
    check.
    """
    found: set[str] = set()
    for path in [packet_dir / "seed.diff", *sorted((packet_dir / "tree").rglob("*"))]:
        if path.is_file() and not path.is_symlink():
            found |= _found_shingles(path, withheld)
    return found


def _is_derived(rel: str) -> bool:
    """Is this packet-relative path one the builder copies rather than writes?"""
    return rel == "seed.diff" or rel.startswith("tree/")


def scan_packet(packet_dir: Path, withheld: Withheld) -> list[str]:
    """Every hit in one packet, as one description per hit."""
    hits = [f"{entry.name}: unexpected top-level entry in the packet"
            for entry in sorted(packet_dir.iterdir())
            if entry.name not in PACKET_ENTRIES]
    accounted = accounted_shingles(packet_dir, withheld)
    for path in sorted(packet_dir.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        rel = path.relative_to(packet_dir).as_posix()
        if rel.startswith(WITHHELD_PREFIXES) or rel in WITHHELD_FILES:
            hits.append(f"{rel}: withheld path present in the packet")
        source = withheld.file_hashes.get(holdout_common.sha256_file(path))
        if source is not None:
            hits.append(f"{rel}: byte-identical to withheld {source}")
        excused = accounted if _is_derived(rel) else set()
        for window in sorted(_found_shingles(path, withheld) - excused):
            hits.append(f"{rel}: 40-char shingle from withheld "
                        f"{withheld.shingles[window]}")
    return hits


def self_test(packet_dir: Path, withheld: Withheld) -> bool:
    """Plant a withheld byte sequence in a copy of a packet and require a fail.

    The plant goes on the end of `task.md`, the packet's own prose, so the
    shingle assertion is the only one that can catch it: a plant in a new file
    would trip the top-level entry assertion and pass this whether or not the
    shingle scan still works. The return requires a hit whose path is
    `task.md` for the same reason, since any hit at all would also be reported
    by a packet that was dirty to begin with.

    Returns True when the check caught the plant. A self-test that fails to
    fail is itself a failure, which is what the caller reports on False.
    """
    # A shingle with no leading or trailing space, so the planted line
    # normalizes back to exactly 40 characters and the scan can match it, and
    # one the packet does not already account for.
    unaccounted = set(withheld.shingles) - accounted_shingles(packet_dir, withheld)
    candidates = sorted(s for s in unaccounted if s.strip() == s)
    if not candidates:
        raise SkepticInfraError(
            "no withheld shingle left to plant: the index is empty, or the "
            "packet already accounts for every shingle in it. Next: confirm "
            "--patches-dir and --tasks-dir name the real corpus."
        )
    plant = candidates[0]
    scratch = Path(tempfile.mkdtemp(prefix="holdout-leakcheck-selftest-"))
    try:
        copy = scratch / packet_dir.name
        shutil.copytree(packet_dir, copy)
        task_md = copy / "task.md"
        task_md.write_text(task_md.read_text() + f"\n{plant}\n")
        return any(hit.startswith("task.md: 40-char shingle")
                   for hit in scan_packet(copy, withheld))
    finally:
        holdout_common.rmtree_readonly(scratch)


def write_packets_yaml(path: Path, task_id: str, digest: str) -> None:
    """Record one packet's digest, leaving the other tasks' entries alone.

    Ruling 7-A: the digests live here rather than in
    `evals/v1/holdout/registry.yaml`, whose `HoldoutVariant` model forbids
    extra keys and cannot gain one without reopening `skeptic/` during the
    verifier revision freeze.

    This lives in the leak check rather than the builder because the digest
    claims a packet carries nothing withheld, and only this script has looked.
    `--record` is the single writer, and it runs only over packets whose scan
    and self-test both came back clean.
    """
    existing = {}
    if path.is_file():
        existing = (yaml.safe_load(path.read_text()) or {}).get("packets") or {}
    existing[task_id] = digest
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"packets": dict(sorted(existing.items()))},
                                   sort_keys=False))


def main() -> None:
    parser = argparse.ArgumentParser(description="Leak-check holdout packets.")
    parser.add_argument("--packet", type=Path, action="append",
                        help="A packet directory. Repeatable. Defaults to every "
                             "directory under --packets-root.")
    parser.add_argument("--packets-root", type=Path,
                        default=Path("workdir") / "holdout" / "packets")
    parser.add_argument("--tasks-dir", type=Path, default=Path("tasks"))
    parser.add_argument("--patches-dir", type=Path, default=Path("patches"))
    parser.add_argument("--self-test", action="store_true",
                        help="Also plant a withheld byte in a copy of each "
                             "packet and require the check to fail.")
    parser.add_argument("--record", action="store_true",
                        help="Write each clean packet's digest to --packets-yaml. "
                             "Implies --self-test, and records nothing for a "
                             "packet with any hit.")
    parser.add_argument("--packets-yaml", type=Path, default=DEFAULT_PACKETS_YAML)
    args = parser.parse_args()
    self_test_wanted = args.self_test or args.record

    if not args.packet and not args.packets_root.is_dir():
        print(f"no packets root at {args.packets_root}. The check reads the "
              f"packet directories `holdout-packet.py` builds under "
              f"<workdir>/holdout/packets/. Next: build a packet, or pass the "
              f"--workdir you built under as --packets-root.", file=sys.stderr)
        raise SystemExit(1)
    packets = args.packet or sorted(p for p in args.packets_root.iterdir() if p.is_dir())
    if not packets:
        print(f"no packet directories under {args.packets_root}. Next: "
              f"`python scripts/holdout-packet.py --task <id>`.", file=sys.stderr)
        raise SystemExit(1)

    try:
        withheld = collect_withheld(args.tasks_dir, args.patches_dir)
    except SkepticInfraError as exc:
        print(f"INFRA ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    failed = False
    for packet_dir in packets:
        clean = True
        hits = scan_packet(packet_dir, withheld)
        for hit in hits:
            print(f"LEAK {packet_dir.name}/{hit}")
        if hits:
            clean = False
        if self_test_wanted and not self_test(packet_dir, withheld):
            print(f"SELF-TEST FAILED {packet_dir.name}: a planted withheld "
                  f"shingle went undetected")
            clean = False
        if not clean:
            failed = True
            continue
        if args.record:
            digest = holdout_common.packet_sha256(packet_dir)
            write_packets_yaml(args.packets_yaml, packet_dir.name, digest)
            print(f"RECORDED {packet_dir.name} {digest}")
    if failed:
        raise SystemExit(1)
    print(f"clean · {len(packets)} packet(s) · {len(withheld.shingles)} withheld "
          f"shingles · {len(withheld.file_hashes)} withheld files")


if __name__ == "__main__":
    main()
