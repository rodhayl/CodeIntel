"""Bounded, anti-symlink reads and writes for local artifacts."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import stat
from typing import Any, Dict

from codeintel.compat import safe_fchmod
from codeintel.core.security import is_symlink_or_reparse


def _lexical_absolute(path: Path) -> Path:
    raw = Path(path).expanduser()
    return Path(os.path.abspath(os.fspath(raw)))


def _open_parent_directory(path: Path, *, label: str) -> tuple[int, str]:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory = getattr(os, "O_DIRECTORY", 0)
    if not nofollow or not directory:
        raise RuntimeError(f"{label} requires O_NOFOLLOW/O_DIRECTORY support")
    candidate = _lexical_absolute(path)
    if not candidate.name:
        raise RuntimeError(f"{label} path must name a file")
    parts = candidate.parent.parts
    if not parts or parts[0] != os.sep:
        raise RuntimeError(f"{label} parent path is not canonical absolute POSIX form")
    fd = os.open(os.sep, os.O_RDONLY | directory | nofollow)
    try:
        for part in parts[1:]:
            if part in ("", ".", ".."):
                raise RuntimeError(f"{label} parent contains unsafe path component")
            next_fd = os.open(part, os.O_RDONLY | directory | nofollow, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        if not stat.S_ISDIR(os.fstat(fd).st_mode):
            raise RuntimeError(f"{label} parent descriptor is not a directory")
        return fd, candidate.name
    except BaseException:
        os.close(fd)
        raise


def _open_regular(path: Path, *, max_bytes: int, label: str) -> tuple[int, os.stat_result]:
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes <= 0:
        raise ValueError("max_bytes must be a positive integer")
    if os.name == "posix":
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        parent_fd = None
        try:
            parent_fd, name = _open_parent_directory(Path(path), label=label)
            fd = os.open(name, flags, dir_fd=parent_fd)
        except OSError as exc:
            raise RuntimeError(f"{label} cannot be opened as a regular non-symlink file: {exc}") from exc
        finally:
            if parent_fd is not None:
                os.close(parent_fd)
    else:
        candidate = _lexical_absolute(path)
        for p in (candidate, *candidate.parents):
            if is_symlink_or_reparse(p):
                raise RuntimeError(f"{label} path contains a symlink or reparse point: {p}")
        try:
            fd = os.open(os.fspath(candidate), os.O_RDONLY | getattr(os, "O_BINARY", 0))
        except OSError as exc:
            raise RuntimeError(f"{label} cannot be opened as a regular non-symlink file: {exc}") from exc
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise RuntimeError(f"{label} must be a regular file")
        if before.st_size > max_bytes:
            raise RuntimeError(
                f"{label} exceeds maximum size: {before.st_size} > {max_bytes} bytes"
            )
        return fd, before
    except BaseException:
        os.close(fd)
        raise


def _assert_unchanged(fd: int, before: os.stat_result, *, label: str, observed_bytes: int) -> None:
    after = os.fstat(fd)
    if (
        before.st_dev != after.st_dev
        or before.st_ino != after.st_ino
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or before.st_ctime_ns != after.st_ctime_ns
    ):
        raise RuntimeError(f"{label} changed while being read")
    if observed_bytes != after.st_size:
        raise RuntimeError(f"{label} byte count changed while being read")


def append_regular_file_nofollow(path: Path, data: bytes, *, label: str) -> None:
    """Append bytes through descriptor-bound directories without following symlinks."""
    if not isinstance(data, bytes):
        raise TypeError("append payload must be bytes")
    if os.name == "posix":
        parent_fd = None
        fd = None
        try:
            parent_fd, name = _open_parent_directory(Path(path), label=label)
            flags = (
                os.O_WRONLY
                | os.O_CREAT
                | os.O_APPEND
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_NONBLOCK", 0)
                | getattr(os, "O_CLOEXEC", 0)
            )
            fd = os.open(name, flags, 0o600, dir_fd=parent_fd)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise RuntimeError(f"{label} must be a regular file")
            safe_fchmod(fd, 0o600)
            view = memoryview(data)
            offset = 0
            while offset < len(view):
                written = os.write(fd, view[offset:])
                if written <= 0:
                    raise RuntimeError(f"{label} append made no progress")
                offset += written
            os.fsync(fd)
        except OSError as exc:
            raise RuntimeError(f"{label} cannot be appended safely: {exc}") from exc
        finally:
            if fd is not None:
                os.close(fd)
            if parent_fd is not None:
                os.close(parent_fd)
    else:
        candidate = _lexical_absolute(path)
        for p in (candidate, *candidate.parents):
            if is_symlink_or_reparse(p):
                raise RuntimeError(f"{label} path contains a symlink or reparse point: {p}")
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_APPEND
            | getattr(os, "O_BINARY", 0)
        )
        fd = None
        try:
            fd = os.open(os.fspath(candidate), flags, 0o600)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise RuntimeError(f"{label} must be a regular file")
            view = memoryview(data)
            offset = 0
            while offset < len(view):
                written = os.write(fd, view[offset:])
                if written <= 0:
                    raise RuntimeError(f"{label} append made no progress")
                offset += written
            os.fsync(fd)
        except OSError as exc:
            raise RuntimeError(f"{label} cannot be appended safely: {exc}") from exc
        finally:
            if fd is not None:
                os.close(fd)


def read_regular_file_bounded(path: Path, *, max_bytes: int, label: str) -> bytes:
    fd, before = _open_regular(path, max_bytes=max_bytes, label=label)
    try:
        chunks: list[bytes] = []
        total = 0
        while True:
            block = os.read(fd, min(1024 * 1024, max_bytes + 1 - total))
            if not block:
                break
            chunks.append(block)
            total += len(block)
            if total > max_bytes:
                raise RuntimeError(f"{label} grew beyond maximum size while being read")
        _assert_unchanged(fd, before, label=label, observed_bytes=total)
        return b"".join(chunks)
    finally:
        os.close(fd)


def sha256_regular_file_bounded(path: Path, *, max_bytes: int, label: str) -> tuple[str, int]:
    fd, before = _open_regular(path, max_bytes=max_bytes, label=label)
    try:
        digest = hashlib.sha256()
        total = 0
        while True:
            block = os.read(fd, min(1024 * 1024, max_bytes + 1 - total))
            if not block:
                break
            total += len(block)
            if total > max_bytes:
                raise RuntimeError(f"{label} grew beyond maximum size while being hashed")
            digest.update(block)
        _assert_unchanged(fd, before, label=label, observed_bytes=total)
        return digest.hexdigest(), total
    finally:
        os.close(fd)


def _reject_nonfinite_json_constant(token: str) -> None:
    raise ValueError(f"non-finite JSON constant {token!r} is not allowed")


def _unique_json_object(pairs: list[tuple[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key {key!r} is not allowed")
        result[key] = value
    return result


def _finite_json_float(token: str) -> float:
    value = float(token)
    if not math.isfinite(value):
        raise ValueError(f"non-finite JSON numeric value {token!r} is not allowed")
    return value


def _require_decoded_utf8(value: Any) -> Any:
    """Reject escaped JSON strings/keys that decode to non-UTF-8 Unicode text."""
    stack = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            for key, child in current.items():
                try:
                    key.encode("utf-8")
                except UnicodeError as exc:
                    raise ValueError("decoded JSON contains non-UTF-8 text") from exc
                stack.append(child)
        elif isinstance(current, list):
            stack.extend(current)
        elif isinstance(current, str):
            try:
                current.encode("utf-8")
            except UnicodeError as exc:
                raise ValueError("decoded JSON contains non-UTF-8 text") from exc
    return value


def strict_json_loads(text: str) -> Any:
    """Decode attestation JSON with unique keys, finite numbers and UTF-8 text."""
    value = json.loads(
        text,
        parse_constant=_reject_nonfinite_json_constant,
        parse_float=_finite_json_float,
        object_pairs_hook=_unique_json_object,
    )
    return _require_decoded_utf8(value)


def read_json_object_bounded(path: Path, *, max_bytes: int, label: str) -> Dict[str, Any]:
    raw = read_regular_file_bounded(path, max_bytes=max_bytes, label=label)
    try:
        payload = strict_json_loads(raw.decode("utf-8", errors="strict"))
    except Exception as exc:
        raise RuntimeError(f"{label} is invalid strict UTF-8 JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} must be a JSON object")
    return payload


__all__ = [
    "append_regular_file_nofollow",
    "read_json_object_bounded",
    "read_regular_file_bounded",
    "sha256_regular_file_bounded",
    "strict_json_loads",
]
