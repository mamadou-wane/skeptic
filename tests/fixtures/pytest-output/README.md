# Captured pytest output

Real stdout and real junit reports, captured at execution time with pytest
9.1.1 on Python 3.12.13. Nothing here is written by hand: `parse_collect_manifest`
and `parse_junit` are parsers, and a parser tested against invented input tests
the author's memory of pytest rather than pytest.

Every sample has a `.cmd` sidecar naming its source tree, the working
directory, the exact command, the pytest version, and the exit code. The
capture roots were `/private/tmp/skeptic-t5-capture/minirepo` and
`/private/tmp/skeptic-t5-capture/click`, which is why those paths appear inside
the error and warning blocks.

One post-capture edit, recorded in the three sidecars it applies to: the junit
reports carried a `hostname` attribute naming the machine that ran them, and
the value is emptied. No repo file reads it.

| Sample | What it holds |
|---|---|
| `*-collect-healthy.txt` | every test collects, exit 0 |
| `*-collect-deselected.txt` | `-k` drops some: `2/4 tests collected (2 deselected)`, exit 0 |
| `*-collect-none.txt` | `-k` drops all: `no tests collected (4 deselected)`, exit 5 |
| `*-collect-error.txt` | an import error under `--continue-on-collection-errors`, exit 1 |
| `*-collect-error-junit.xml` | the junit for that run: a `<testcase>` carrying `<error message="collection failure">` |
| `minirepo-marks-junit.xml` | a skip, an xfail, a non-strict xpass, and a plain pass |

Two sources. The minirepo is `tests/fixtures/minirepo`, materialized by
`tests/helpers.make_minirepo_task`. The click samples come from pallets/click at
`5aa8ac43527f91c4c801a50b485c09576715d340`, the commit `tasks/click-0001.yaml`
pins, scoped to `tests/test_utils/test_make_default_short_help.py` so the
manifest stays readable; its parametrized nodeids carry brackets, spaces, and
commas, which is the shape a naive line parser gets wrong.

The collect-error and marks samples were produced by adding a module to a
throwaway copy of the tree: `tests/test_broken.py` imports a module that does
not exist, `tests/test_warns.py` defines a test class with an `__init__` so the
run also carries a warnings summary, and `tests/test_marks.py` carries the four
mark outcomes. The minirepo collect-error sample is the one with both trailing
blocks, so it is what pins the rule that everything after the manifest is
ignored.
