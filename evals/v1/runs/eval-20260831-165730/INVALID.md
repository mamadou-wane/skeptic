# Invalid v1.0.1 revalidation execution

Preserved for audit, excluded from every published comparison.

The venv console entry point resolved the main checkout rather than the hotfix
worktree. `manifest.json` records verifier `8d30a6fa4d44` and collector `1`;
the intended code under test is verifier `28550e55c4ee`, collector `4`.

This snapshot contains 53 Eval A rows and 11 INFRA rows after the session key's
credit balance was exhausted. Recorded spend is $2.1347. No row was retried.
See `docs/ai-log/0028-v1.0.1-evaluation-revalidation.md`.
