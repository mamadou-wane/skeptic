# 0021: the installs pin to the closure the runs measured

Date: 2026-08-29
PR: #21 (351e5a0, 09b5ff5, 89fad92, merged bc82e34).
Agent: Claude Code (Fable 5), with an Opus review of the diff before push.
Produced: `constraints/click.txt` and `constraints/rich.txt`, read out of the
images the published runs used; `EnvironmentSpec.constraints` and the field
on all twelve task yamls; `PIP_CONSTRAINT` through the image's resolve stage
and the venv lane, each reading its closure back; the tag hashing the closure
bytes; cache keys that drop the field when absent; six tests; DECISIONS row
231 and the mechanism paragraph in docs/architecture.md.
Human: Mamadou reviewed, agreed with the four choices named in the writeup,
asked that the guarantee be worded as scoped to the verified environment, and
authorized the merge on green CI. Claude merged on that instruction.
Verification: ruff clean; full suite as one invocation, 1073 passed and 1
skipped; both corpus images rebuilt under the pin with each freeze equal to
its pin; fresh venvs for click-0001 and rich-0001 passing the read-back; a
venv seeded with pygments 2.21.0 reading 2.20.0 after one setup under the
pin; CI green on 89fad92 and on the merge.

Row 225 had named both the gap and the fix a week earlier: unpinned installs
let pygments 2.21.0 turn eight rich rendering tests red on every tree, voided
five holdout re-rolls, and forced every holdout verdict to be re-produced in
corpus-era venvs. The Docker lane had frozen a closure since row 71 and
never committed it, so the freeze pinned a machine and not the corpus. The
images that machine built are still here, and the pin is their freeze, which
is what lets no published figure move: the closures are the ones the runs
measured, and the manifests' image ids stay as recorded.

Prediction record. I declared the branch ready off three test modules and a
full-suite run that was still going. The review, run in parallel, found the
suite red on eight tests and one real regression the tests did not name: the
new field put `constraints: null` into `environment.model_dump()`, which the
BUILD cache key hashes, and that key is the one not salted by
`verifier_revision`, so every cached attempt in a live workdir would have
been paid again. `exclude_none=True` at the three key sites keeps an
undeclared pin hashing as the field's absence did, and
`test_attempt_one_keeps_the_pre_attempt_key` stays green with its literal
untouched. Two stubs missing the new argument and a tag test that read the
pin off cwd were the other six failures. Run the whole fast lane before
calling a branch ready; a reviewer should not be the first to run it.

One claim was dropped rather than carried: that pip propagates the variable
into isolated PEP 517 build environments. It was not measured, and it does
not matter to what is verified. The guarantee is scoped to the resulting
environment, the image's closure byte for byte and the venv's installed set
as a subset of the pin, and a claim about transient build dependencies waits
for a test that proves it.
