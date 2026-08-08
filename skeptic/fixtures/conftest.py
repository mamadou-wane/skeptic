# The bundled minirepo carries its own tests/test_minirepo.py and
# tests/test_golden.py, which a bare `pytest skeptic/` would otherwise
# collect: two files named tests/test_minirepo.py in one run is an
# import-file-mismatch, and the bundled suite is the demo's target, not a
# test of skeptic. Same guard as tests/fixtures/hacks/conftest.py, same
# reason.
collect_ignore_glob = ["*"]
