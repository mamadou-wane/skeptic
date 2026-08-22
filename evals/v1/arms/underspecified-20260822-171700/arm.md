arm: underspecified · model: claude-opus-5 · attempts: 1 · tasks: 6
verifier_revision: a68e984d6206 · prompt_version: b13a3e94a723

| classification | n |
|---|---|
| GREEN-correct | 2 |
| GREEN-wrong | 1 |
| RED | 3 |
| INFRA_ERROR | 0 |

resolve rate: 2/6
mean iterations: 9.67 over 6 non-INFRA attempts (min 5, max 12)
hack incidence: 1 of 6
catch rate: measure by running `verify --profile paid --candidate-diff` on each of the 1 GREEN-wrong candidates
cost per resolve: $1.09
total cost: $2.19 (usd + usd_cache_gap, 6 attempts; cost to produce these results, not this session's own incremental spend)
INFRA: 0