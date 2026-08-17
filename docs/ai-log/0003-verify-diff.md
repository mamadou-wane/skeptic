# 0003: verify --diff, the spec-free audit path

Date: 2026-08-17
PR: #3 (887d320fbfa6442c4d45ce5ec403e336b5389d13)
Agent: Claude Code (Fable 5) as controller under subagent-driven
development; an Opus 5 implementer built it from a controller brief, an
Opus 5 task reviewer attacked the diff, a Sonnet 5 re-reviewer verified the
fix round with independent runs.
Produced: skeptic/diffmode.py (backend gate, environment inference, spec
synthesis), the optional seed patch through spec/collector/verify, the
diff-lane CLI surface and refusals, tag_slug sanitization, and the docker
e2e pair: a benign covered diff PASSes at score zero, a test-deleting diff
FAILs citing t1_collect/H1, with no task spec anywhere.
Human: Mamadou reviewed and merged, and trimmed the PR body's gate wording;
PR descriptions stop naming the em-dash check from here on.
Verification: ruff clean, fast suite 815, docker suite 83 passed / 1
skipped, task-mode cache keys and checks/ untouched, both e2e runs
re-verified by the re-reviewer from their artifacts.
Prediction record. The implementer's suite was fully green while four real
defects stood, all found by the task review: two reachable diff-mode
failure paths spoke task-mode language (a non-applying patch told the user
to report a harness bug; a second audit at a branchless commit said to fix
repo.commit in a task spec that does not exist), the relaxed bug_patch
schema silently dropped the corpus guard the yamls relied on, and the
benign e2e accepted any verdict so an always-FAIL regression would have
stayed green. The reviewer also mutation-tested the review itself: it
deleted the diff lane's clone call to prove its replacement was exercised.
Green suites measure what their assertions ask; the review measured what
the assertions missed.
