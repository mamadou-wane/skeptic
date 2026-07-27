sample: minirepo-collect-none.txt
source: the minirepo fixture, materialized by tests/helpers.make_minirepo_task
cwd: /private/tmp/skeptic-t5-capture/minirepo
command: python -m pytest --collect-only -q --continue-on-collection-errors -p no:cacheprovider -k no_such_selector
pytest: pytest 9.1.1
exit: 5
