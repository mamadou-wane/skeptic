# 0022: the footprint, measured with the caches a stranger lacks emptied

Date: 2026-08-29
PR: #22 (5058c5d, adc5f70, merged 1020bd1).
Agent: Claude Code (Fable 5), with an Opus review of the diff before push.
Produced: `scripts/footprint.py`, the record
`evals/v1/footprint/footprint-20260829.json`, the table under The lanes in
docs/evaluation.md with what the run does not cover stated beside it, the
README's clone and venv lines and its measured sentence, the architecture
anchor quoting SWE-bench's README at a permalink, `tests/test_footprint.py`,
DECISIONS row 232.
Human: Mamadou reviewed, accepted the narrowing of the plan's "clean
container" to a clean checkout, venv and build cache on the host, authorized
the merge on green CI, and asked that no Claude Code session link appear in
any PR body, commit or doc from here on. The three PR bodies were stripped
and this PR's commits rewritten before merge; eight earlier commits on main
still carry the trailer, since removing them means a force-push of main.
Verification: ruff clean; full suite as one invocation, 1077 passed and 1
skipped; CI green on both the push and the pull request run.

The number: 67 s from a fresh public clone of bc82e34 to the first real
Docker verdict, pull excluded, build cache pruned; 11 s to the demo's two
verdicts with no Docker and no key. The M7 bar was ten minutes.

Prediction record. The first measurement of the day read 45 s for the cold
verify and I wrote it up as cold. It was not: removing an image tag leaves
Docker's build cache intact, and the whole resolve stage, installs included,
replayed from it. The 6 s image builds earlier that day should have said so.
The review caught it, and the pruned run reads 57 s. Two more things the
review found that I had not: the plan's criterion was narrowed without a
sentence saying so, and a disk total counted the base image twice because
`docker image inspect` sizes include inherited layers. A draft that proxied
the base image pull with a cross-platform pull by digest and then removed
the reference untagged this machine's base image; it was restored by pulling
the digest again and the corpus images kept their layers, and the pull is now
excluded and labelled rather than proxied.

The withdrawn number. `docs/architecture.md` had cited SWE-bench at "roughly
120 GB of disk and 15 to 50 minutes to a first eval" since M5. The README,
the evaluation guide and swebench.com were read for the duration and none
states one. The hardware sentence is now quoted at a blob permalink with its
read date and the scope named on both sides; the duration is gone from the
page and struck from the plan's line 36. The rule is row 229's: a figure with
no committed or citable source does not stay on the page because it is
convenient.
