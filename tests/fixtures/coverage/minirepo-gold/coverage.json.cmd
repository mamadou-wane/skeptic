samples: coverage.json, coverage.sql
source: the minirepo fixture, materialized by tests/helpers.make_minirepo_task, seeded and then gold-patched (the candidate side of the gold pair)
capture: collector.observe_variant(spec, image.tag, tree, artifacts, "candidate", ("minirepo.py", "tests/test_minirepo.py"))
image: skeptic-repo-minirepo-upstream:ff1a5e6bead0-533af5b9, cwd /workspace, COVERAGE_RCFILE=/artifacts/coveragerc
suite: python -m coverage run -m pytest -q --continue-on-collection-errors --junitxml=/artifacts/junit.xml -o junit_family=xunit1
report: python -m coverage json --show-contexts --include=minirepo.py,tests/test_minirepo.py -o /artifacts/coverage.json
dump: sqlite3 .coverage .dump > coverage.sql, taken from the data file that same run wrote to /artifacts/.coverage
rc: [run] data_file = /artifacts/.coverage · source = . · branch = false · dynamic_context = test_function · relative_files = true
versions: coverage 7.15.2 (data schema 7), pytest 9.1.1, Python 3.12.13 (image constraints.txt)
exit: 0 for every step, 4 passed
note: the two samples come from one run. coverage.json is the per-file half read_coverage reads; coverage.sql rebuilds the data file it reads run_contexts from. The data file ships as a text dump rather than as the 53 KB binary, so the context table is readable in a diff. tests/test_collector.py replays it with sqlite3 executescript into a tmp directory next to a copy of the JSON.
note: the include list carries two files where a real gold run scopes to one, since the gold patch touches minirepo.py alone. The second file is what makes the scoping assertion in tests/test_collector.py non-vacuous: read_coverage is handed a report of two files and asked for one. The dump is wider again, four measured files and five contexts, which is what run_contexts is for: it describes the run rather than the patch.
note: contexts are pytest's module name, so they read test_minirepo.test_parse_range_basic rather than tests.test_minirepo.test_parse_range_basic. tests/ carries no __init__.py, and dynamic_context = test_function names the function by its importable module.
