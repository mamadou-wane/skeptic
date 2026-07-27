sample: minirepo-collect-error.txt
source: the minirepo fixture, materialized by tests/helpers.make_minirepo_task, plus tests/test_broken.py (bad import) and tests/test_warns.py
cwd: /private/tmp/skeptic-t5-capture/minirepo
command: python -m pytest --collect-only -q --continue-on-collection-errors -p no:cacheprovider
pytest: pytest 9.1.1
exit: 1
