class SkepticInfraError(Exception):
    """Operational failure: environment, tooling, or IO broke.

    Never converted into check evidence or a verdict. Callers exit with
    code 3. The message must state: what failed, why Skeptic needs it,
    and the exact next command to run.
    """


class VenvBuildRefused(Exception):
    """Raised when a BUILD-stage action is attempted on the venv runner."""


class EvidenceValidationError(SkepticInfraError):
    """A check result or a verdict did not satisfy the frozen evidence schema.

    Section 10 requires the aggregate INFRA case to carry the schema path in
    its message, and the base class already routes it to exit 3, so that
    requirement is message formatting rather than a second error path.
    Nothing in M3 raises it: the schema lands with the checks and the
    aggregator that validates their combined output lands at M4.
    """
