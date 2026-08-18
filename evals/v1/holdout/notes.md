# The blind holdout's authoring run

Run 2026-08-18, all sessions `gpt-5.6-sol` through codex-cli 0.147.0. Every
claim below traces to a file in this directory; the full narrative is the
DECISIONS.md row for this PR.

## What is here

- `packets.yaml`: one digest per authoring packet, written only by
  `scripts/holdout-leakcheck.py --record` and only for packets whose scan and
  self-test both came back clean. All 12 recorded, 50551 withheld shingles,
  102 withheld files. Packets are not committed: the builder is deterministic
  (verified by rebuild, twice) and the digest is the provenance.
- `sessions/`: 18 authoring sessions, 12 first attempts and 6 re-rolls. Per
  attempt: the JSONL transcript, the stderr stream beside it, and a sidecar
  with the exact argv, the model, the codex version, the scratch `CODEX_HOME`
  and its contents, the packet digest taken before the session, and the
  feedback string given (empty on attempt 1).
- `audit/`: every transcript audited for reads outside its packet
  (`scripts/holdout-audit.py`, packets root widened to the workdir). In 17 of
  18 sessions every flagged event is the session's own `out/patch.diff`
  apply-check resolving inside the packet. The exception is
  `rich-0002-holdout-h10-a2`: it searched the disk, found the screen's
  leftover per-task venvs, and ran the packet's own test files with the
  system interpreter and that venv. Every flag is committed. Nothing
  withheld was reachable on those paths: the venvs hold each task's seeded
  install and public dependencies, never `acceptance/`, non-seed patches, or
  detector code. The prompt's "you cannot run the tests" premise did not
  hold for that session and the record says so.
- `patches/`: all 18 authored diffs, admitted or not.
- `screen/`: one verdict json per screened attempt, produced in the
  corpus-era venvs (pygments 2.20.0). `first-run-fresh-venv-pygments-2.21/`
  preserves the first pass, which ran in freshly built venvs where pygments
  2.21.0 (released after the corpus gate; the task installs are unpinned)
  turns 8 rich rendering tests red on any tree. A control caught it: the
  committed `rich-0001-h6` variant, certified green by `seed --check`,
  screened tests-red there. In the corpus-era venvs both controls behave
  (h6 admits, gold reads correct-fix) and every published verdict was
  re-produced.

## The one protocol repair, disclosed

Five re-roll sessions (rich-0001, rich-0003, rich-0004, rich-0005,
rich-0006) were triggered by the contaminated first-pass verdicts: their
attempt-1 patches screen ADMITTED in the true environment, so the protocol
never reaches an attempt 2 for them. Those five re-rolls are VOID: their
patches, transcripts and sidecars stay committed, and none is a variant.
rich-0002's re-roll stands, because its attempt 1 is tests-red in both
environments, so the string it received was the true condition. Six feedback
strings were sent in total against the twelve the screen's leak pricing
allows; five of the six rode the false premise above.

## Result, pending adjudication

11 of 12 tasks admitted on attempt 1: every click task and rich-0001,
rich-0003, rich-0004, rich-0005, rich-0006. rich-0002 (H10, the only
golden-dirs task) failed both attempts on the true tests-red condition and
drops; the attempt-2 patch also broke on its own terms in the first pass
(a conftest syntax error, pytest exit 4). The holdout therefore publishes
at n=11 with H10 unrepresented, per the spec's "a task with zero admitted
variants publishes as n<12 with the count."

Owner adjudication of the 11 admitted diffs comes next; `registry.yaml` is
written after it, with any relabels recorded.
