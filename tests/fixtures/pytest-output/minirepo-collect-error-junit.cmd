sample: minirepo-collect-error-junit.xml
source: the minirepo fixture, materialized by tests/helpers.make_minirepo_task, plus tests/test_broken.py (bad import) and tests/test_warns.py
cwd: /private/tmp/skeptic-t5-capture/minirepo
command: python -m pytest -q --continue-on-collection-errors -p no:cacheprovider --junitxml=collect-error-junit.xml -o junit_family=xunit1
pytest: pytest 9.1.1
exit: 1
edit: the hostname attribute value was emptied after capture (house rule: no personal details in the repo). Nothing else was touched, and parse_junit reads file, name, classname, type, and message only.
