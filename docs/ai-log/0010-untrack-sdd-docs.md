# 0010: stop publishing the SDD plans and specs

Date: 2026-08-22
PR: #10 (f169143, merged c2d8caf).
Agent: Claude Code (Opus 5).
Produced: `docs/superpowers/` added to `.gitignore` and its 12 files
untracked, 8 milestone plans and 4 design specs. They stay on disk, so local
workflow is unaffected.
Human: Mamadou asked for the change and chose the full untrack over the two
narrower readings offered, which were ignoring the directory going forward
while leaving the 12 tracked, or untracking the plans and keeping the specs.
He reviewed and merged.
Verification: nothing reads those paths at runtime, checked by grep for
`read_text`, `open` and `Path(` against them across `skeptic/`, `tests/` and
`scripts/`. Fast suite 955 on the branch, which forks from main below task 8's
three new tests.
Cost, recorded rather than hidden: 14 citations now point at documents a reader
cannot open, 10 of them in `DECISIONS.md`, including row 224 ratifying the M6
spec by path. Two of the other four are docstrings in `skeptic/` and were left
unedited on purpose. Touching anything under `skeptic/` moves
`verifier_revision()` off `a68e984d6206`, the revision the holdout run was
measured at and the one M6's pressure arms have to share.
