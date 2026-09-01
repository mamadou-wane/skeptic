"""Containment-safe admission and reading of execution artifacts.

Candidate-authored output remains untrusted until it has crossed this module's
descriptor-relative admission boundary into host-owned sealed storage.
"""
from __future__ import annotations

import errno
import os
import stat
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from skeptic.errors import SkepticInfraError

CONTROL_MAX = 4 * 1024
TEXT_MAX = 8 * 1024 * 1024
STRUCTURED_MAX = 16 * 1024 * 1024
COVERAGE_DATA_MAX = 64 * 1024 * 1024
COVERAGE_JSON_MAX = 1_610_612_736

_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_NONBLOCK = getattr(os, "O_NONBLOCK", 0)
_ROOT_FLAGS = os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC
_SOURCE_FLAGS = os.O_RDONLY | _NOFOLLOW | _CLOEXEC | _NONBLOCK
_TEMP_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _CLOEXEC
_CHUNK_BYTES = 64 * 1024


@dataclass(frozen=True)
class ArtifactSpec:
    relative_path: str
    max_bytes: int
    required: bool = True


def _infra(what: str, why: str, next_step: str) -> SkepticInfraError:
    return SkepticInfraError(f"{what}: {why}. Next: {next_step}.")


def _parts(relative_path: str) -> tuple[str, ...]:
    if not isinstance(relative_path, str) or not relative_path:
        raise _infra(
            "Artifact path is empty",
            "an admitted artifact must name a POSIX-relative file beneath its root",
            "fix the declared artifact path",
        )
    if "\x00" in relative_path:
        raise _infra(
            f"Artifact path {relative_path!r} contains a NUL byte",
            "the operating system cannot safely open that declared name",
            "fix the declared artifact path",
        )
    path = PurePosixPath(relative_path)
    if path.is_absolute():
        raise _infra(
            f"Artifact path {relative_path!r} is absolute",
            "an absolute name can escape the declared artifact root",
            "use a POSIX-relative artifact path",
        )
    if ".." in path.parts:
        raise _infra(
            f"Artifact path {relative_path!r} contains parent traversal",
            "a '..' component can escape the declared artifact root",
            "remove parent traversal from the declared artifact path",
        )
    parts = tuple(part for part in path.parts if part != ".")
    if not parts:
        raise _infra(
            f"Artifact path {relative_path!r} does not name a file",
            "the artifact root itself cannot be admitted as an artifact",
            "name a file beneath the artifact root",
        )
    return parts


def _cap(max_bytes: int, relative_path: str) -> None:
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes < 0:
        raise _infra(
            f"Artifact {relative_path!r} has invalid byte cap {max_bytes!r}",
            "artifact limits must be non-negative integer byte counts",
            "set the artifact's maximum byte count to a non-negative integer",
        )


def _is_symlink(parent_fd: int, name: str) -> bool:
    try:
        return stat.S_ISLNK(os.stat(name, dir_fd=parent_fd, follow_symlinks=False).st_mode)
    except OSError:
        return False


def _root_is_symlink(root: Path) -> bool:
    try:
        return stat.S_ISLNK(os.lstat(root).st_mode)
    except OSError:
        return False


def _missing(relative_path: str) -> SkepticInfraError:
    return _infra(
        f"Required artifact {relative_path!r} is missing",
        "the execution did not produce its declared output",
        "inspect the execution logs and re-run the phase",
    )


def _open_root(root: Path, relative_path: str, required: bool, role: str) -> int | None:
    try:
        return os.open(root, _ROOT_FLAGS)
    except FileNotFoundError:
        if not required:
            return None
        raise _missing(relative_path) from None
    except OSError as exc:
        if exc.errno == errno.ELOOP or _root_is_symlink(root):
            raise _infra(
                f"Cannot open {role} artifact root {root}",
                "the root is a symbolic link and cannot define a containment boundary",
                "recreate the artifact root as a host-owned directory",
            ) from exc
        raise _infra(
            f"Cannot open {role} artifact root {root}",
            f"the root is not a no-follow directory ({exc.strerror or exc})",
            "recreate the artifact root as a host-owned directory and re-run",
        ) from exc


def _open_source(
    root: Path,
    relative_path: str,
    *,
    required: bool,
) -> int | None:
    parts = _parts(relative_path)
    root_fd = _open_root(root, relative_path, required, "source")
    if root_fd is None:
        return None
    directory_fds = [root_fd]
    try:
        for component in parts[:-1]:
            try:
                child_fd = os.open(component, _ROOT_FLAGS, dir_fd=directory_fds[-1])
            except FileNotFoundError:
                if not required:
                    return None
                raise _missing(relative_path) from None
            except OSError as exc:
                if exc.errno == errno.ELOOP or _is_symlink(directory_fds[-1], component):
                    raise _infra(
                        f"Cannot admit artifact {relative_path!r}",
                        f"parent component {component!r} is a symbolic link",
                        "replace it with a real directory inside the artifact root",
                    ) from exc
                raise _infra(
                    f"Cannot admit artifact {relative_path!r}",
                    f"parent component {component!r} is not a no-follow directory",
                    "repair the artifact directory shape and re-run the phase",
                ) from exc
            directory_fds.append(child_fd)

        parent_fd = directory_fds[-1]
        final_name = parts[-1]
        try:
            before_open = os.stat(final_name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            if not required:
                return None
            raise _missing(relative_path) from None
        except OSError as exc:
            raise _infra(
                f"Cannot inspect artifact {relative_path!r}",
                f"descriptor-relative metadata lookup failed ({exc.strerror or exc})",
                "repair the artifact output and re-run the phase",
            ) from exc
        if stat.S_ISLNK(before_open.st_mode):
            raise _infra(
                f"Cannot admit artifact {relative_path!r}",
                "the final component is a symbolic link",
                "produce the declared output as a regular file",
            )
        if not stat.S_ISREG(before_open.st_mode):
            raise _infra(
                f"Cannot admit artifact {relative_path!r}",
                "the final component is not a regular file",
                "produce the declared output as a regular file",
            )
        try:
            source_fd = os.open(final_name, _SOURCE_FLAGS, dir_fd=parent_fd)
        except FileNotFoundError:
            if not required:
                return None
            raise _missing(relative_path) from None
        except OSError as exc:
            if exc.errno == errno.ELOOP or _is_symlink(parent_fd, final_name):
                raise _infra(
                    f"Cannot admit artifact {relative_path!r}",
                    "the final component is a symbolic link",
                    "produce the declared output as a regular file",
                ) from exc
            raise _infra(
                f"Cannot open artifact {relative_path!r}",
                f"the final component could not be opened without following links "
                f"({exc.strerror or exc})",
                "repair the artifact output and re-run the phase",
            ) from exc
        try:
            opened = os.fstat(source_fd)
        except OSError as exc:
            os.close(source_fd)
            raise _infra(
                f"Cannot inspect opened artifact {relative_path!r}",
                f"descriptor metadata lookup failed ({exc.strerror or exc})",
                "repair the artifact output and re-run the phase",
            ) from exc
        if not stat.S_ISREG(opened.st_mode):
            os.close(source_fd)
            raise _infra(
                f"Cannot admit artifact {relative_path!r}",
                "the opened final component is not a regular file",
                "produce the declared output as a regular file",
            )
        return source_fd
    finally:
        for directory_fd in reversed(directory_fds):
            os.close(directory_fd)


def _check_opened_size(source_fd: int, relative_path: str, max_bytes: int) -> None:
    try:
        size = os.fstat(source_fd).st_size
    except OSError as exc:
        raise _infra(
            f"Cannot inspect opened artifact {relative_path!r}",
            f"descriptor metadata lookup failed ({exc.strerror or exc})",
            "repair the artifact output and re-run the phase",
        ) from exc
    if size > max_bytes:
        raise _infra(
            f"Artifact {relative_path!r} exceeds its {max_bytes}-byte cap",
            f"the opened regular file reports {size} bytes",
            "reduce the artifact size or use its approved typed cap",
        )


def _source_chunks(source_fd: int, relative_path: str, max_bytes: int) -> Iterable[bytes]:
    total = 0
    while True:
        remaining_probe = max_bytes + 1 - total
        try:
            chunk = os.read(source_fd, min(_CHUNK_BYTES, remaining_probe))
        except OSError as exc:
            raise _infra(
                f"Cannot read artifact {relative_path!r}",
                f"the opened regular file could not be streamed ({exc.strerror or exc})",
                "repair the artifact output and re-run the phase",
            ) from exc
        if not chunk:
            return
        total += len(chunk)
        if total > max_bytes:
            raise _infra(
                f"Artifact {relative_path!r} exceeds its {max_bytes}-byte cap",
                "the file grew beyond its approved cap while it was being admitted",
                "reduce the artifact size or use its approved typed cap",
            )
        yield chunk


def _byte_chunks(data: bytes) -> Iterable[bytes]:
    for offset in range(0, len(data), _CHUNK_BYTES):
        yield data[offset:offset + _CHUNK_BYTES]


def _open_destination_root(sealed_root: Path, relative_path: str) -> int:
    try:
        os.mkdir(sealed_root, 0o700)
    except FileExistsError:
        pass
    except OSError as exc:
        raise _infra(
            f"Cannot create sealed artifact root {sealed_root}",
            f"the host-owned destination could not be created ({exc.strerror or exc})",
            "repair the run directory and re-run the phase",
        ) from exc
    root_fd = _open_root(sealed_root, relative_path, True, "sealed destination")
    assert root_fd is not None
    return root_fd


def _destination_directory_fds(
    sealed_root: Path,
    relative_path: str,
    parts: tuple[str, ...],
) -> list[int]:
    directory_fds = [_open_destination_root(sealed_root, relative_path)]
    try:
        for component in parts[:-1]:
            parent_fd = directory_fds[-1]
            try:
                child_fd = os.open(component, _ROOT_FLAGS, dir_fd=parent_fd)
            except FileNotFoundError:
                try:
                    os.mkdir(component, 0o700, dir_fd=parent_fd)
                except FileExistsError:
                    pass
                except OSError as exc:
                    raise _infra(
                        f"Cannot create destination for artifact {relative_path!r}",
                        f"parent component {component!r} could not be created "
                        f"({exc.strerror or exc})",
                        "repair the sealed run directory and re-run the phase",
                    ) from exc
                try:
                    child_fd = os.open(component, _ROOT_FLAGS, dir_fd=parent_fd)
                except OSError as exc:
                    if exc.errno == errno.ELOOP or _is_symlink(parent_fd, component):
                        raise _infra(
                            f"Cannot publish artifact {relative_path!r}",
                            f"destination parent {component!r} is a symbolic link",
                            "recreate the sealed destination as host-owned directories",
                        ) from exc
                    raise _infra(
                        f"Cannot open destination for artifact {relative_path!r}",
                        f"parent component {component!r} is not a no-follow directory",
                        "repair the sealed run directory and re-run the phase",
                    ) from exc
            except OSError as exc:
                if exc.errno == errno.ELOOP or _is_symlink(parent_fd, component):
                    raise _infra(
                        f"Cannot publish artifact {relative_path!r}",
                        f"destination parent {component!r} is a symbolic link",
                        "recreate the sealed destination as host-owned directories",
                    ) from exc
                raise _infra(
                    f"Cannot open destination for artifact {relative_path!r}",
                    f"parent component {component!r} is not a no-follow directory",
                    "repair the sealed run directory and re-run the phase",
                ) from exc
            directory_fds.append(child_fd)
        return directory_fds
    except BaseException:
        for directory_fd in reversed(directory_fds):
            os.close(directory_fd)
        raise


def _write_all(destination_fd: int, data: bytes) -> None:
    remaining = memoryview(data)
    while remaining:
        written = os.write(destination_fd, remaining)
        if written == 0:
            raise OSError(errno.EIO, "zero-byte write to artifact temporary")
        remaining = remaining[written:]


def _publish_chunks(
    sealed_root: Path,
    relative_path: str,
    max_bytes: int,
    chunks: Iterable[bytes],
) -> None:
    parts = _parts(relative_path)
    _cap(max_bytes, relative_path)
    directory_fds = _destination_directory_fds(sealed_root, relative_path, parts)
    destination_fd = directory_fds[-1]
    temp_name = f".skeptic-{uuid.uuid4().hex}.tmp"
    temporary_fd: int | None = None
    try:
        try:
            temporary_fd = os.open(temp_name, _TEMP_FLAGS, 0o600, dir_fd=destination_fd)
            total = 0
            for chunk in chunks:
                total += len(chunk)
                if total > max_bytes:
                    raise _infra(
                        f"Artifact {relative_path!r} exceeds its {max_bytes}-byte cap",
                        "the data stream is larger than its approved typed cap",
                        "reduce the artifact size or use its approved typed cap",
                    )
                _write_all(temporary_fd, chunk)
            os.fsync(temporary_fd)
        except SkepticInfraError:
            raise
        except OSError as exc:
            raise _infra(
                f"Cannot stage sealed artifact {relative_path!r}",
                f"the create-exclusive temporary could not be written ({exc.strerror or exc})",
                "repair the sealed run directory and re-run the phase",
            ) from exc
        finally:
            if temporary_fd is not None:
                os.close(temporary_fd)
                temporary_fd = None

        try:
            os.link(
                temp_name,
                parts[-1],
                src_dir_fd=destination_fd,
                dst_dir_fd=destination_fd,
                follow_symlinks=False,
            )
        except FileExistsError:
            raise _infra(
                f"Sealed artifact {relative_path!r} already exists",
                "no-replace publication refuses to alter an artifact owned by an earlier phase",
                "use a fresh sealed output directory and re-run",
            ) from None
        except OSError as exc:
            raise _infra(
                f"Cannot publish sealed artifact {relative_path!r}",
                f"atomic no-replace publication failed ({exc.strerror or exc})",
                "repair the sealed run directory and re-run the phase",
            ) from exc
        try:
            os.unlink(temp_name, dir_fd=destination_fd)
            temp_name = ""
        except OSError as exc:
            raise _infra(
                f"Cannot finish publishing sealed artifact {relative_path!r}",
                f"the unique temporary name could not be removed ({exc.strerror or exc})",
                "repair the sealed run directory before continuing",
            ) from exc
    finally:
        if temporary_fd is not None:
            os.close(temporary_fd)
        if temp_name:
            try:
                os.unlink(temp_name, dir_fd=destination_fd)
            except FileNotFoundError:
                pass
            except OSError:
                pass
        for directory_fd in reversed(directory_fds):
            os.close(directory_fd)


def admit_artifacts(
    quarantine_root: Path,
    sealed_root: Path,
    specs: Sequence[ArtifactSpec],
) -> None:
    """Admit declared regular files from quarantine into sealed storage."""
    for artifact in specs:
        _cap(artifact.max_bytes, artifact.relative_path)
        source_fd = _open_source(
            quarantine_root,
            artifact.relative_path,
            required=artifact.required,
        )
        if source_fd is None:
            continue
        try:
            _check_opened_size(source_fd, artifact.relative_path, artifact.max_bytes)
            _publish_chunks(
                sealed_root,
                artifact.relative_path,
                artifact.max_bytes,
                _source_chunks(source_fd, artifact.relative_path, artifact.max_bytes),
            )
        finally:
            os.close(source_fd)


def publish_artifact_bytes(
    sealed_root: Path,
    relative_path: str,
    data: bytes,
    max_bytes: int,
) -> None:
    """Publish host-captured bytes with the same atomic no-replace boundary."""
    _cap(max_bytes, relative_path)
    if len(data) > max_bytes:
        raise _infra(
            f"Artifact {relative_path!r} exceeds its {max_bytes}-byte cap",
            f"the host-captured stream contains {len(data)} bytes",
            "reduce the captured output or use its approved typed cap",
        )
    _publish_chunks(sealed_root, relative_path, max_bytes, _byte_chunks(data))


def read_artifact_bytes(
    root: Path,
    relative_path: str,
    max_bytes: int,
    *,
    required: bool = True,
) -> bytes | None:
    """Read one artifact without following any declared-path component."""
    _cap(max_bytes, relative_path)
    source_fd = _open_source(root, relative_path, required=required)
    if source_fd is None:
        return None
    try:
        _check_opened_size(source_fd, relative_path, max_bytes)
        return b"".join(_source_chunks(source_fd, relative_path, max_bytes))
    finally:
        os.close(source_fd)


def read_artifact_text(
    root: Path,
    relative_path: str,
    max_bytes: int,
    *,
    required: bool = True,
) -> str | None:
    """Read one UTF-8 text artifact through the safe byte reader."""
    data = read_artifact_bytes(root, relative_path, max_bytes, required=required)
    if data is None:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _infra(
            f"Artifact {relative_path!r} is not valid UTF-8 text",
            f"decoding failed at byte {exc.start}",
            "inspect the producing phase and re-run it with UTF-8 output",
        ) from exc


def validate_artifact_path(
    root: Path,
    relative_path: str,
    max_bytes: int,
    *,
    required: bool = True,
) -> Path | None:
    """Validate a sealed artifact for a path-based trusted host library."""
    _cap(max_bytes, relative_path)
    parts = _parts(relative_path)
    source_fd = _open_source(root, relative_path, required=required)
    if source_fd is None:
        return None
    try:
        _check_opened_size(source_fd, relative_path, max_bytes)
    finally:
        os.close(source_fd)
    return root.joinpath(*parts)
