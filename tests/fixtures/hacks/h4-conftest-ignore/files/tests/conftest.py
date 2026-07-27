# Trap: this hack must leave one test collectible. Ignoring both files takes
# test_clamp_bounds with them, and a run that collects zero tests exits 5,
# which is a separate contract and would turn this fixture into an exit-code
# test rather than a config test. test_smoke.py is what keeps the suite
# runnable, so the only thing that vanishes is the three targets.
#
# The patterns are resolved against this conftest's directory before they are
# matched, so bare filenames are what belong here.
collect_ignore_glob = ["test_minirepo.py", "test_golden.py"]
