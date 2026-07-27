# The corpus is post-hack file bodies, and several of them are named
# test_*.py. Skeptic's own suite collects tests/ and would import them: two
# files named tests/test_minirepo.py in one run is an import-file-mismatch
# error, and a body written to be red is red here too. Nothing under this
# directory is a test of Skeptic. tests/test_hack_fixtures.py reads the bodies
# as text and runs them in a workspace.
collect_ignore_glob = ["*"]
