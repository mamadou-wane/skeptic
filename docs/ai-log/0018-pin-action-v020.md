# 0018: pin the Action snippet to v0.2.0

Date: 2026-08-22
PR: #18 (e174953, merged e0cc4c2).
Agent: Claude Code (Opus 5).
Produced: one line. The README's copy-paste Action snippet moves from `@v0.1.1`
to `@v0.2.0`.
Human: Mamadou asked for the tag and reviewed and merged the pin.
Verification: full suite 1060 passed, 1 skipped, as one invocation.

v0.2.0 was tagged at 48e17c1 and released the same day, after CI reported
success on that exact commit rather than after a local suite passed. That
distinction is the whole lesson of 0017 and it is why the tag waited.

The pin followed the release rather than leading it, which row 228 had already
committed to: a snippet naming a tag that does not exist breaks for every
consumer who copies it, so bumping the package and moving the snippet are two
steps with a release in between. `action.yml` is byte-identical between the two
tags, so what actually moves for a consumer is the CLI underneath, including
the fix for `skeptic --version` reporting 0.1.0 through the whole v0.1.1
release.
