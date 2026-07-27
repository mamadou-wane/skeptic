sample: click-collect-error.txt
source: pallets/click at 5aa8ac43527f91c4c801a50b485c09576715d340, plus tests/test_broken.py (bad import)
cwd: /private/tmp/skeptic-t5-capture/click
command: python -m pytest --collect-only -q --continue-on-collection-errors -p no:cacheprovider tests/test_utils/test_make_default_short_help.py tests/test_broken.py
pytest: pytest 9.1.1
exit: 1
