# Invalid v1.0.1 revalidation execution

Preserved for audit, excluded from the v1.0.1 three-run comparison.

The venv console entry point resolved the main checkout rather than the hotfix
worktree. All three runs returned SUSPECT 1.00 without cache replay, but they
executed verifier `8d30a6fa4d44`, collector `1`, not verifier `28550e55c4ee`,
collector `4`. Recorded spend was $0.0474, $0.0466, and $0.0484.

See `docs/ai-log/0028-v1.0.1-evaluation-revalidation.md`.
