sample: minirepo-marks-junit.xml
source: the minirepo fixture, materialized by tests/helpers.make_minirepo_task, plus tests/test_marks.py (a skip, an xfail, a non-strict xpass, a plain pass)
cwd: /private/tmp/skeptic-t5-capture/minirepo-marks
command: python -m pytest -q -p no:cacheprovider tests/test_marks.py --junitxml=marks-junit.xml -o junit_family=xunit1
pytest: pytest 9.1.1
exit: 0
edit: the hostname attribute value was emptied after capture (house rule: no personal details in the repo). Nothing else was touched, and parse_junit reads file, name, classname, type, and message only.
