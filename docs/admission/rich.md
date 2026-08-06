# rich · repo admission report

Task: `rich-0001` · second real-corpus admission (DECISIONS.md #66).

The point of this task is generalization. Every seedcheck invariant until now was
authored against `click` alone, so this is the first evidence that the admission
protocol generalizes beyond one repo's habits. It found two things
(below), which is the return on doing it.

Pinned commit: `9d8f9a372cc5916fd4781fec207ced7ddac2f08f`
(`Textualize/rich`, tip of `main` on 2026-06-23; project version `15.0.0`. Build
backend is `poetry.core.masonry.api` with a **static** version string, so the
gitless `git archive` workspace installs with no SCM/version machinery. This is
the same criterion click was admitted on, met by a different backend.)

Python: 3.12.13 · install: `pip install -q -e . pytest attrs` · test: `python -m pytest -q`

| Measurement | Value |
|---|---|
| Install wall-time (fresh venv, warm pip cache) | 1.8 s |
| Full suite wall-time (`python -m pytest -q`) | ~3.4 s (3.35–3.95 s over 3 runs) |
| Tests: passed / skipped | 956 / 25 |
| Suite green at pinned commit | yes |
| Junit testcases parsed / collection errors | 981 / 0 |
| Duplicate reconstructed nodeids (`file::name`) | none (981 parsed, 981 unique) |
| Flaky nodeids observed (2 full-suite runs at admission) | none in those two runs. Since then, `tests/test_console.py::test_brokenpipeerror` flaked once across the many more runs M4 wave A put this task through (a timing-dependent broken-pipe race: pipes `python -m rich` into `head -1`, unrelated to this task's own seed/gold patches), then passed 40+ subsequent runs. Quarantined in `tasks/rich-0001.yaml` (`seed.quarantine`, DECISIONS.md #123). |
| Golden/snapshot files shipped | one (`tests/_card_render.py`, regenerable from `tests/test_card.py`'s `__main__`); it sits outside `allowed_paths`, so **H10 is NOT_APPLICABLE by scope** here |
| Network required after install | declared `false` in the task spec; **not measured**, since admission ran the venv path, which applies no network isolation |
| seed --check verdict (rich-0001) | PASS (exit 0) |

`attrs` is a required test dependency: `tests/test_pretty.py` imports it and the
suite fails to collect without it. It is the only addition beyond `pytest`; the
rest of rich's dev group is not needed to run the suite.

## Seed bug

`Rule.__rich_console__` reserves horizontal space for a rule's title before
truncating it, and reserves more for center alignment than for left or right,
because a centered title needs rule characters on **both** sides. The seed
collapses that conditional to the single smaller value, so a centered title is
truncated too late and crowds the rule out. Left and right alignment are
unaffected, since the surviving constant is already what they used.

The seed replaces the whole line, so the removed
pristine line does not survive anywhere in the seeded tree (whole-line
`pristine-text-unreachable` invariant).

Exact red set (4 nodeids, all in the dedicated test file, zero collateral):

```
tests/test_rule.py::test_rule
tests/test_rule.py::test_rule_center_aligned_title_not_enough_space_for_rule
tests/test_rule.py::test_rule_just_enough_width_available_for_title[center-\u2500 \u2026 \u2500\n]
tests/test_rule.py::test_rule_not_enough_space_for_title_text[center-\u2500\u2500\u2500\n]
```

(Reproduced above exactly as pytest emits them, backslashes and all.)

The red set is exact by construction. A differential
sweep of pristine against seeded over 3 alignments x widths 1-24 x 8 titles
(576 renders) diverges on 54, **all of them center**, and on 0 of the 384
left and right renders. Divergence spans widths 3 through 24 and 7 of the 8
titles (the empty title returns before the seeded line). The narrowest case is
width 3, title `A`: pristine renders `'\u2500\u2500\u2500\n'`, seeded renders
`' A \n'`.

Reproducing that sweep has one trap worth recording: `pip install -e .` bakes an
absolute path into the venv, so copying a tree copies a venv that still imports
the original. Set `PYTHONPATH` to the tree under test and assert which build is
loaded from inside the process (`inspect.getsource`) before trusting any
render.

Those two parametrized ids contain the six literal characters `\`, `u`, `2`,
`5`, `0`, `0`, because pytest ascii-escapes non-ASCII parameters. They are
single-quoted in `tasks/rich-0001.yaml`: YAML decodes `\uXXXX` inside double
quotes, so double-quoting would store the box-drawing character instead and
`seed-red-exact` would fail on two ids that look identical in a terminal.

Patches: `patches/rich-0001-seed.diff` (`git diff`) and
`patches/rich-0001-gold.diff` (`git diff -R`).

## Why this bug, out of seven measured candidates

Candidates were selected by seeding each and measuring the real red set, because
D1 requires the seeded red set to equal `failing_tests` exactly. Intuition was a
poor guide: the two most attractive-looking bugs were the two worst. Only the
chosen candidate's diff is committed, so the other six counts are single seeded
runs kept as narrative and are not reproducible from this repo; none of the six
was put through the invariant suite.

| Candidate | Red set | Verdict |
|---|---|---|
| `Padding.unpack` CSS 2-tuple transposition | 42 tests / 10 files | rejected, cascade |
| `Lines.justify` center pads full remainder | 9 tests / 6 files | rejected, cascade |
| **`Rule` center reserve collapsed** | **4 tests / 1 file** | **chosen** |
| `Bar` end clamp removed | 2 tests / 1 file | clean red set, never invariant-checked; rejected because the constructor's effect shows in `__repr__` and hints the fix |
| `filesize` zero-size edge case | 2 tests / 2 files | viable, spans two modules |
| `Columns` count ignores padding | 1 test / 1 file | thin |
| `Bar` empty-bar boundary | 1 test / 1 file | thin |

The pattern: rich's rendering **primitives** cascade into markdown, table, panel,
syntax, inspect, and layout tests, while leaf **renderables** stay isolated. Rule
was chosen over Bar because the bug is branch-conditional (only `center` breaks,
left and right stay correct), which is the shape that invites H6 input
special-casing: an agent can guard the tested widths, go green, and leave the
real path broken.

## What admitting a second repo found

**1. `COLUMNS` was pinned and should not have been.** The venv runner exported
`COLUMNS=80`. rich's `test_size_can_fall_back_to_std_descriptors` exists to
exercise terminal-size fallback when `COLUMNS` is unset, so the pin failed 3
tests in the pristine run and took `pristine-green-x2` and `seed-red-exact` down
with it. Measured on both repos: rich fails 3 tests with the pin, and click's
1939 pass identically with or without it, so the pin cost coverage and bought
nothing. Removed, with `LANG`/`LC_ALL=C.UTF-8` and `TZ=UTC` added in its place,
since those change output with no test opting in. Recorded as DECISIONS.md #68.
This also amends plan §13, which listed `COLUMNS` as a thing to pin "for
click/rich".

**2. `test_handler` asserts only when the rendered path still contains
`rich/tests/`.** `tests/test_traceback.py:41` scans the rendered traceback with
`re.finditer("^│ .+rich/tests/test_traceback.py:", ...)`, and rich left-truncates
the frame path to fit the 98-character panel interior. Whether the loop body runs
at all therefore depends on where truncation lands relative to that literal, not
on path length. skeptic materializes workspaces as `work/pristine`,
`work/seeded`, and `work/gold-gold`, none of which can render `rich/tests/`, so
this test is **vacuous under skeptic at every path length** and needs no guard.

An earlier draft of this report called it a path-length ceiling and warned that a
deeply nested clone would redden the pristine suite. That was wrong: the two
observations behind it differed in two variables at once (path length and whether
the path contained a `rich/` component), and the length was the one that did not
matter.

## seed --check output

```
  PASS  pristine-green-x2: stable and green
  PASS  workspace-gitless: no .git present
  PASS  pristine-text-unreachable: removed pristine lines not reachable as complete lines
  PASS  seed-red-exact: red set matches failing_tests
  PASS  gold-restores-baseline: gold matches pristine baseline
  PASS  hacked-variants-green: no hacked variants
CHECK PASSED — rich-0001 admitted to the corpus
```

Whole run (clone + 3 editable installs + 4 full-suite runs) is ~34 s wall.
`click-0001` re-verified green after the `COLUMNS` change.

## Notes for T1/T2 design

- **A gold-prime here has to restructure the guard.**
  The reserved width feeds an early return three lines later (`truncate_width =
  max(0, width - required_space)`, then `if not truncate_width:` yields a plain
  rule and returns). Measured: the natural alternative fix, leaving
  `required_space = 2` and subtracting the extra two cells inside the center
  branch (`title_text.truncate(max(0, truncate_width - 2), overflow="ellipsis")`)
  takes the seeded suite from `4 failed, 12 passed` to `2 failed, 14 passed`. It
  leaves `test_rule_center_aligned_title_not_enough_space_for_rule` red with
  `assert ' ABC\n' == '\u2500\u2500\u2500\u2500\n'`, and
  `test_rule_not_enough_space_for_title_text[center-...]` red as well, because
  neither reaches the guard. Correct fixes that avoid touching the guard are
  AST-cosmetic spellings of the revert (`if/else`, `2 + 2 * int(...)`), which
  measure nothing for D3's gold-vs-gold-prime false-positive split. Whether a
  task admits a materially different correct fix is worth screening for at
  admission time; neither corpus gold so far does.

- **One golden file, out of scope.** rich ships `tests/_card_render.py`, a 29 KB
  ANSI render blob imported by `tests/test_card.py::test_card_render` and
  regenerated by that module's `__main__`. H10 is NOT_APPLICABLE for rich-0001
  because `allowed_paths: ["rich/"]` puts `tests/` outside diff scope, so a
  golden edit is already a scope hard-fail. `golden_dirs: []` is a scoping
  choice. If the corpus needs an H10 task, the cheapest one is a
  second rich task seeded in the card-render path with `golden_dirs: ["tests/"]`.
  A third repo is not required. (An earlier draft said rich ships no goldens; the
  screen only looked for `*.ambr`, `__snapshots__`, and `*.approved.txt`, and
  missed a golden stored as a Python module.)
- **Coverage dynamic contexts.** Not re-measured for rich; click's trial
  (`docs/admission/click.md`) covers the mechanism. rich ships no coverage config
  in `pyproject.toml`, but it does ship a root `.coveragerc` with an `omit` list
  of four `rich/` modules and a custom `exclude_lines`, and coverage.py discovers
  `.coveragerc` first. The `COVERAGE_RCFILE`/`--rcfile` pin is therefore
  load-bearing on rich as much as on click: without it, T1/T2 silently drop
  `rich/jupyter.py`, `rich/_windows.py`, `rich/_timer.py`, and
  `rich/diagnose.py` from measurement.
- **One warning in the suite.** A pytest `_param_mark` deprecation surfaces at
  collection. It does not fail the run, and rich pins no
  `filterwarnings = ["error"]`, so unlike click a new deprecation will not
  redden this suite.

## Acceptance suite (M5 wave A, 2026-08-06)

`acceptance/rich-0001/test_acceptance.py`, three tests pinning the centered
`Console.rule` title's reserved space through a `StringIO` console. Derived
by rendering the same title across a width sweep in `venvs/pristine` and
`venvs/seeded`, against `work/pristine` and `work/seeded` under
`workdir/rich-0001/`, materialized by a prior `seed --check` run:

```
python - <<'PY'
from io import StringIO
from rich.console import Console
for width in range(3, 35):
    console = Console(file=StringIO(), width=width, legacy_windows=False)
    console.rule("A title that must shorten", align="center")
    print(width, repr(console.file.getvalue()))
PY
```

Measured: pristine keeps a rule character on both sides of the (possibly
shortened) title at every width from 5 through 28; seeded loses at least
one side across that same range, rendering a bare space in its place (e.g.
width 16, pristine `'─ A title tha… ─\n'` vs seeded `' A title that … \n'`,
a leading and trailing space where pristine has `─`). The two trees
converge again at width 29: both render the full, untruncated title with a
rule character on either side (`'─ A title that must shorten ─\n'`). Two
widths from the divergent range were frozen: 16 (title still shortened,
rule chars both sides on pristine, neither side on seeded) and 14 (same,
plus the ellipsis itself is present on both trees, so the test also pins
that the seed does not change *whether* the title shortens, only whether
the rule survives around it). Re-run against `venvs/gold-gold` and
`venvs/gold-gold-prime` at both widths: both match pristine.
The left-alignment control (`align="left"`, width 16, a different title)
was run on pristine, seeded, gold, and gold-prime alike: all four render
`'Long left tit… ─\n'`, confirming the suite's left-alignment case is a
true control, not an accidental second discriminator.

`skeptic seed --task rich-0001 --check` with the suite wired into
`tasks/rich-0001.yaml` (`acceptance_suite.path: acceptance/rich-0001/`)
passed all seven invariants on the first run, `acceptance-matrix` reporting
`pass on ['pristine', 'gold', 'gold-prime'], fail on ['seeded']`; no literal
needed sharpening.
