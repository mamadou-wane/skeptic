class SkepticInfraError(Exception):
    """Operational failure: environment, tooling, or IO broke.

    Never converted into check evidence or a verdict. Callers exit with
    code 3. The message must state: what failed, why Skeptic needs it,
    and the exact next command to run.
    """


class VenvBuildRefused(Exception):
    """Raised when a BUILD-stage action is attempted on the venv runner."""
