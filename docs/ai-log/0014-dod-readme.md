# 0014: the Definition-of-Done gaps

Date: 2026-08-22
PR: #14 (d452c3e, merged 610f64d).
Agent: Claude Code (Opus 5), scoped by a ten-agent survey of §16 against the
README, CI, the four inherited defects and the repo's stale claims.
Produced: three tradeoffs the DoD names and the README had never argued
(prevention versus detection, mutation budget, model routing), the
prompt-injection limit, a mermaid architecture diagram, a roadmap with the four
named items, and two corrections.
Human: Mamadou reviewed and merged.
Verification: ruff clean, fast 963, mermaid validated as a flowchart, no figure
touched so both figure-trace tests passed unchanged.

The gap worth naming is prevention versus detection. `grep -i prevent
README.md` returned nothing, so a reader could not tell that six of the ten
categories are refused by the sandbox rather than caught by a detector, or that
`verify --diff` has no such mount and turns all ten into detection. The page had
been leaning on that distinction for two milestones without stating it.

Two corrections rode along. The license paragraph covered the 65 diffs under
`patches/` but not the 18 under `evals/v1/holdout/patches/`, which carry
upstream click and rich source hunks too: legal weight rather than cosmetic.
And the quickstart told a first-time reader to install without the constraint
file that CI and `skeptic doctor` both use.
