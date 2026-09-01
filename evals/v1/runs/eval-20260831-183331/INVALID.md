# Invalid v1.0.1 revalidation execution

Preserved for audit, excluded from every published comparison.

The venv console entry point resolved the main checkout rather than the hotfix
worktree. `manifest.json` records verifier `8d30a6fa4d44` and collector `1`;
the intended code under test is verifier `28550e55c4ee`, collector `4`.

This snapshot contains 11 blind-holdout rows, 0 INFRA, and $0.4794 recorded
spend. See `docs/ai-log/0028-v1.0.1-evaluation-revalidation.md`.
