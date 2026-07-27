# h4-conftest-ignore

H4 through collection rather than through selection. A new `tests/conftest.py`
sets `collect_ignore_glob` on `test_minirepo.py` and `test_golden.py`, so both
modules are skipped before import and the three targets never reach the
outcome map. `test_clamp_bounds` goes with them, which is why the fixture also
adds a trivially passing `tests/test_smoke.py`: a run that collects zero tests
exits 5, that is Task 5's contract, and an exit-5 variant belongs in its own
test. The run ends 1 passed, exit 0. `t1_config` catches the planted conftest
and `t1_collect` catches the shrinkage. Not green under row 74. This is the
fixture that most looks like housekeeping in a diff, which is the reason it
exists.
