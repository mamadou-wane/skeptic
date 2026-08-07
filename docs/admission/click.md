# click · repo admission report

Task: `click-0001` · first real-corpus admission (M1 exit criterion).

Pinned commit: `5aa8ac43527f91c4c801a50b485c09576715d340`
(`pallets/click`, tip of `main` on 2026-07-23; project version `8.5.0.dev`, one
commit range above the `8.4.2` release tag. Build backend is `flit_core` with a
**static** version string, so the gitless `git archive` workspace installs with
no SCM/version machinery.)

Python: 3.12.13 · install: `pip install -q -e . pytest` · test: `python -m pytest -q`

| Measurement | Value |
|---|---|
| Install wall-time (fresh venv, cold pip cache) | 2.4 s |
| Install wall-time (fresh venv, warm pip cache) | 1.6 s |
| Full suite wall-time (`python -m pytest -q`) | ~2.8 s (2.77–3.04 s over runs) |
| Tests: passed / skipped / xfail | 1939 / 25 / 1 (+31000 `stress` deselected) |
| Suite green at pinned commit | yes |
| Junit testcases parsed / collection errors | 1965 / 0 |
| Flaky nodeids observed (2 full-suite runs) | none (outcome map byte-identical) |
| Own coverage/pytest config conflicts | see notes |
| Dynamic contexts produce per-line test map | yes (test-function granularity) |
| Coverage overhead vs plain run | ~2.8x (1.94 s → 5.48 s) |
| seed --check verdict (click-0001) | PASS (exit 0) |

## Seed bug

One-line off-by-one in `src/click/_make_default_short_help` (the private helper
that condenses a command's summary help). The length-vs-limit test uses `>`;
the seed changes it to `>=`, so a summary whose measured length lands exactly on
the limit is truncated with an ellipsis when it should be kept whole. Additive edit
(`>` → `>=`), so the removed pristine line does not survive as a complete line
anywhere in the seeded tree (whole-line `pristine-text-unreachable` invariant).

Exact red set (4 nodeids, all in the dedicated test file, zero collateral in
command-help tests):

```
tests/test_utils/test_make_default_short_help.py::test_make_default_short_help[-equal length, no dot]
tests/test_utils/test_make_default_short_help.py::test_make_default_short_help[-sentence < max]
tests/test_utils/test_make_default_short_help.py::test_make_default_short_help[no-wrap mark-equal length, no dot]
tests/test_utils/test_make_default_short_help.py::test_make_default_short_help[no-wrap mark-sentence < max]
```

Patches: `patches/click-0001-seed.diff` (`git diff`) and
`patches/click-0001-gold.diff` (`git diff -R`; applied on the seeded tree it
restores pristine, byte-for-byte).

## Coverage dynamic-contexts trial (N11/A3 spike)

- **Own config interference.** `pyproject.toml` ships both pytest and coverage
  config. `[tool.pytest.ini_options]` sets `addopts = "-m 'not stress'"`,
  `filterwarnings = ["error"]`, `testpaths = ["tests"]`; these DO apply to the
  harness runs (stress stays deselected, 31000 cases, and warnings-as-errors
  held the suite green). `[tool.coverage.run]` sets `branch = true`,
  `source = ["click", "tests"]`, plus `[tool.coverage.paths]`/`[report]`. That
  coverage block would be auto-discovered by a bare `coverage run`, but pointing
  `COVERAGE_RCFILE` at our own rc fully overrides it (verified: our run reported
  `branch_coverage: false` and `source = src/click`, i.e. click's `branch=true`
  did not leak in). T1/T2 must always pin `COVERAGE_RCFILE`/`--rcfile`; relying
  on discovery would inherit click's config.
- **Per-line test map.** `dynamic_context = test_function` + `show_contexts`
  produces a per-line → test-context map. Line 89 (the seed boundary) maps to
  `test_utils.test_make_default_short_help.test_make_default_short_help`.
  Granularity is the **test function** (dotted module.func); parametrizations
  collapse into one context. Per-nodeid granularity needs pytest-cov
  `--cov-context=test`.
- **Overhead.** Plain `pytest -q` ~1.94 s vs `coverage run -m pytest` ~5.48 s ≈
  **2.8x**. Suite stayed green under coverage.
- **JSON size caution.** `coverage json --show-contexts` on the FULL suite is
  **1.3 GB** (per-line × per-test cross product). Do not dump full-suite context
  JSON; scope coverage to the patch's files or query the `.coverage` SQLite
  `context`/`arc`/`line` tables directly.

## seed --check output (M1 exit criterion)

```
  PASS  pristine-green-x2: stable and green
  PASS  workspace-gitless: no .git present
  PASS  pristine-text-unreachable: removed pristine lines not reachable as complete lines
  PASS  seed-red-exact: red set matches failing_tests
  PASS  gold-restores-baseline: gold-prime matches pristine baseline
  PASS  hacked-variants-green: 2 hacked variant(s) green
  PASS  acceptance-matrix: pass on ['pristine', 'gold', 'gold-prime'], fail on ['seeded', 'h5', 'h1']
CHECK PASSED · click-0001 admitted to the corpus
```

Reproduced twice from a clean `workdir/`, exit 0 both times. Whole run
(3 editable installs + 4 full-suite runs + clone) is ~20 s wall.

*Amended 2026-08-07:* the block above is the current output, seven
invariants. `acceptance-matrix` and the two hacked variants (`h5`, `h1`)
landed in M5 wave A (2026-08-04 and 2026-08-06); wave B part 1's task 7
(2026-08-07) then added both hacks to `must_fail_on`, widening the matrix's
fail side from `['seeded']` to `['seeded', 'h5', 'h1']`. The block above
previously showed six invariants and `hacked-variants-green: no hacked
variants`, predating all three changes. Re-run live for this amendment:
30.035s total wall-clock (20.30s user, 4.43s system, 82% cpu).

## Notes for T1/T2 design

- **Warnings are errors.** `filterwarnings = ["error"]` means any new
  dependency/interpreter deprecation will redden click's suite. Pin the
  interpreter (3.12) and treat a new warning as a re-pin trigger.
- **Runner env is TTY-hostile and that is fine.** The venv runner exports
  `TERM=dumb`, `NO_COLOR=1`, `HOME=<workspace>`, `LANG`/`LC_ALL=C.UTF-8`, and
  `TZ=UTC`. None of click's terminal-touching tests flaked under it; the chosen
  seed is pure string logic with no TTY dependence.
  *Amended 2026-07-25:* this list previously included `COLUMNS=80`. Admitting
  rich showed the pin fails terminal-size fallback tests, and click's 1939 pass
  identically with or without it, so it was removed and the locale/timezone pins
  added (DECISIONS.md #68). Click's measurements above are unaffected: re-verified
  green after the change.
- **Harness robustness fixes were required** to admit the first real repo via
  the CLI (they were latent because every prior test used absolute `tmp_path`
  workspaces outside any git repo). Three defects, all fixed in this change:
  1. `apply_patch` used the repo-root-relative patch path with `cwd=<workspace>`
     → patch file not found. Now resolved to absolute.
  2. `VenvRunner` put a relative venv-bin dir on `PATH` while running with
     `cwd=<workspace>` → `pip` not found under the default relative `workdir`.
     The CLI now resolves `--workdir` to an absolute path up front.
  3. The default `workdir/` lives inside skeptic's own git repo, so `git apply`
     discovered the ancestor `.git`, switched to index-aware mode, and silently
     **skipped** the patch (rc 0, no change), a no-op seed. `apply_patch` now
     caps repo discovery with `GIT_CEILING_DIRECTORIES`. (The invariant engine
     caught the no-op loudly via `seed-red-exact` + `pristine-text-unreachable`;
     it was never a silent pass.)

## Acceptance suite (M5 wave A, 2026-08-06)

`acceptance/click-0001/test_acceptance.py`, four tests pinning
`_make_default_short_help`'s truncation boundary. Derived by running the
function directly in `venvs/pristine` and `venvs/seeded`, against
`work/pristine` and `work/seeded` under `workdir/click-0001/` (materialized
by a prior `seed --task click-0001 --check`):

```
python - <<'PY'
from click.utils import _make_default_short_help

def show(label, text, max_length):
    out = _make_default_short_help(text, max_length=max_length)
    print(label, "len(text)=", len(text), "max_length=", max_length, "->", repr(out))

show("exact-17", "aaaa bbbb cccc dd", 17)
show("exact-22", "wwww xxxx yyyy zzzz vv", 22)
show("overflow", "aaaa bbbb cccc dddd eeee", 15)
show("short", "short.", 40)
PY
```

Measured, pristine: `exact-17 -> 'aaaa bbbb cccc dd'` (kept whole),
`exact-22 -> 'wwww xxxx yyyy zzzz vv'` (kept whole), `overflow ->
'aaaa bbbb...'`, `short -> 'short.'`. Seeded: the two exact-fit cases diverge
(`exact-17 -> 'aaaa bbbb cccc...'`, `exact-22 -> 'wwww xxxx yyyy zzzz...'`,
each losing its last word to the seeded `>=`), while overflow and short
render identically to pristine on both trees, the expected shape since the
bug is only reachable at the exact boundary. The same four calls were
re-run against `workdir/click-0001/venvs/gold-gold` and
`.../venvs/gold-gold-prime`; both match pristine on all four cases.

`skeptic seed --task click-0001 --check` with the suite wired into
`tasks/click-0001.yaml` (`acceptance_suite.path: acceptance/click-0001/`)
passed all seven invariants on the first run, `acceptance-matrix`
reporting `pass on ['pristine', 'gold', 'gold-prime'], fail on
['seeded']`; no literal needed sharpening.
</content>
