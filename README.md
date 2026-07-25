# Skeptic

Your coding agent says the tests pass. Skeptic checks whether that means anything.

## Status

M1 foundations, merged. What runs today:

```
skeptic seed --task click-0001 --check
```

That materializes a gitless `git archive` workspace from a pinned `pallets/click`
commit, builds a venv, applies the seed patch, and enforces six corpus-admission
invariants: `pristine-green-x2`, `workspace-gitless`, `pristine-text-unreachable`,
`seed-red-exact`, `gold-restores-baseline`, `hacked-variants-green`. All six pass.

Limits today. The corpus holds one admission task against a criterion of two. The
second lands with M2's corpus thread and must be a different repo, because every
invariant so far was authored against `click` alone. Digest-pinned images are
deferred to M2 with the BUILD stage, so `--runner venv` (verify-only, reduced
isolation) is the only wired runner, and `--runner docker` refuses with an INFRA
exit.

Nothing downstream of admission exists: no Builder loop, no detection checks, no
verdict, no `verify --diff`. Those are M2 through M6.

## Why the design works

Seeding bugs into known-correct code at a pinned commit makes that commit a hidden
reference implementation, which turns verification into differential testing with a
free oracle. The workspace is a `git archive` export with no `.git`, so the pristine
fix is unreachable from inside the sandbox.

## Layout

| Path | What |
|---|---|
| `skeptic/` | CLI, spec loader, workspace materializer, venv/docker sandbox, seedcheck engine, trace writer, stage cache |
| `tasks/` | Corpus task specs |
| `patches/` | Seed and gold diffs per task |
| `docs/admission/` | Per-repo admission reports with pinned commits |
| `docs/skeptic-engineering-plan.md` | The plan |
| `DECISIONS.md` | Decision provenance, including recorded dissents |

Python 3.12. `pip install -e ".[dev]" && pytest`.
