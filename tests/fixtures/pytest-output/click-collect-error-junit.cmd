sample: click-collect-error-junit.xml
source: pallets/click at 5aa8ac43527f91c4c801a50b485c09576715d340, plus tests/test_broken.py (bad import)
cwd: /private/tmp/skeptic-t5-capture/click
command: python -m pytest -q --continue-on-collection-errors -p no:cacheprovider --junitxml=collect-error-junit.xml -o junit_family=xunit1 tests/test_utils/test_make_default_short_help.py tests/test_broken.py
pytest: pytest 9.1.1
exit: 1
edit: the hostname attribute value was emptied after capture (house rule: no personal details in the repo). Nothing else was touched, and parse_junit reads file, name, classname, type, and message only.
