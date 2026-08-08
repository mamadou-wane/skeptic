"""The bundled minirepo, shared by the self-tests and `skeptic demo` (DX1).

It lives inside the package because the demo must work from a plain install
with no checkout. `root()` resolves through the installed package rather than
__file__ arithmetic so a zip-safe install still finds it.
"""
from importlib.resources import files
from pathlib import Path


def root() -> Path:
    return Path(str(files("skeptic.fixtures")))
