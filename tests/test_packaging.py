"""The bundled fixture tree ships its non-Python payload in a real wheel.

Code inspection is not evidence for a packaging claim: these tests read
`skeptic.fixtures.root()` the same way an installed-from-wheel caller would,
and the wheel-build verification in the task report is what actually proves
setuptools' package-data glob picked up the non-.py files.
"""
import json

import skeptic.fixtures


def test_bundled_fixture_ships_its_non_python_payload():
    """A wheel that carries only the .py files is the dangerous half-install.

    setuptools' package finder ships modules, not data. Without an explicit
    package-data entry, skeptic/fixtures/minirepo/tests/test_minirepo.py
    installs while pyproject.toml and goldens/expected.txt silently do not,
    and the demo fails at runtime rather than at build time.
    """
    root = skeptic.fixtures.root()
    for rel in ("minirepo/pyproject.toml", "minirepo/goldens/expected.txt",
                "demo.json", "demo/h1-excision.diff"):
        assert (root / rel).is_file(), rel


def test_demo_manifest_matches_the_bundled_diffs():
    manifest = json.loads((skeptic.fixtures.root() / "demo.json").read_text())
    for entry in manifest.values():
        assert (skeptic.fixtures.root() / entry["diff"]).is_file()
        assert entry["changed_files"]
