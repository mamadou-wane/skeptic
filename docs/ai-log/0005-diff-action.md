# 0005: the report-only diff-audit Action

Date: 2026-08-17
PR: #5 (5411913b2768c8e09981c5b3ef90f5a3fab2294a)
Agent: Claude Code (Fable 5) as controller; a Sonnet 5 implementer, an
Opus 5 task reviewer, a Sonnet 5 re-reviewer.
Produced: action.yml (report-only composite action, injection-safe,
binary-safe, scratch under RUNNER_TEMP), scripts/rescore-deterministic.py
deriving the deterministic lane's 16/29 lenient with strict 12/29
unchanged, the README CI-patch-audit section with a drift test binding the
published figures to the script's output, and the 14/29 worst-case bound
for losing t1_scope.
Human: Mamadou reviewed and merged.
Verification: ruff clean, fast suite 812, the rescore independently
reproduced by both reviewer and re-reviewer, every action expression
verified out of run bodies by a structural test, every run block bash -n
clean.
Prediction record. The task review caught the Action silently failing on
any PR touching a binary file (git diff without --binary emits a dataless
patch), a textbook template-injection surface in the run bodies of an
action whose product is auditing patches for what they hide, and a README
sentence that misstated coverage_zero's trigger in the exact paragraph
justifying the report-only default. The fix round's own YAML quoting bug
(unquoted expressions in flow-style env maps) was caught by the
implementer's structural test before it shipped. The spec's "~20-line
Action" became 80 lines, every one above the sketch bought by a named
finding; the sketch was the wrong number, and the honest response was to
publish why rather than to hit it.
