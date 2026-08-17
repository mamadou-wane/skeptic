# 0002: skeptic doctor and the shared Docker diagnosis

Date: 2026-08-17
PR: #2 (f3924840ad69bcabf5eab0c111f6d834bcda7856)
Agent: Claude Code (Fable 5) implementing; a two-lens Opus 5 review of the
staged diff before the PR opened.
Produced: skeptic/doctor.py (five checks, five-outcome key probe),
sandbox.py's docker_diagnosis ladder with docker_available delegating to it,
the rerouted build/verify refusals with platform-aware next commands, the
~40-seam test migration, 36 new tests, the full 31-package lock
regeneration, and the README status line.
Human: Mamadou reviewed and merged. No new rulings; the spec's PR 2 section
carried the design.
Verification: ruff clean, fast suite 771, docker suite 81 passed / 1
skipped, live-fired keyless (exit 3), keyed (exit 0, real zero-token probe),
and with a trailing-space key (malformed, names the tr fix).
Prediction record. The pre-review implementation carried two Blockers its
own 756-green suite could not see, because every test monkeypatched the
probe out: a key with a trailing newline died locally inside the HTTP
client and was reported as a network outage with the user sent to
status.anthropic.com, and a broken anthropic install tracebacked doctor
with zero lines printed. The review also refuted the disk floor's
derivation (it summed image bytes that live on Docker's volume against a
check of the workdir volume) and caught a cli-plugin warning shape that
would have misread a stopped daemon as a socket-permission problem. All
folded before the PR opened; the misdiagnosis class the command exists to
prevent is the class the review caught it committing.
