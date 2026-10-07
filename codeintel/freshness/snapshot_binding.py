"""Bind production parser/indexer reads to the exact STAGING snapshot bytes.

The generation barrier registers the immutable path->SHA256 contract captured before
indexing. Consumers reopen repository files through descriptor-relative O_NOFOLLOW
walks and reject any byte stream that no longer matches that contract.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
import os
from pathlib import Path, PurePosixPath
import stat
import threading
from typing import Dict, Mapping, Optional

from codeintel.core.contracts import (
    MAX_INDEX_SOURCE_FILE_BYTES,
)
from codeintel.core.security import FreshnessBusyError
from codeintel.freshness.errors import SourceAvailabilityError, SourceLimitError, SourceValidationError

@dataclass
class _SnapshotRegistration:
    hashes: Dict[str, str]
    owners: set[object]


_SNAPSHOTS: Dict[tuple[Optional[str], str], _SnapshotRegistration] = {}
_SNAPSHOT_LOCK = threading.RLock()
_HEX = frozenset("0123456789abcdef")


def _canonical_rel_path(rel_path: str) -> str:
    if not isinstance(rel_path, str) or not rel_path or "\x00" in rel_path or "\\" in rel_path:
        raise ValueError("snapshot path must be a canonical POSIX repository-relative path")
    path = PurePosixPath(rel_path)
    parts = path.parts
    if (
        not parts
        or path.is_absolute()
        or any(part in ("", ".", "..") for part in parts)
        or str(path) != rel_path
    ):
        raise ValueError("snapshot path must be canonical and traversal-free")
    return rel_path


def _canonical_sha256(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("snapshot SHA256 must be text")
    digest = value.lower()
    if len(digest) != 64 or any(ch not in _HEX for ch in digest):
        raise ValueError("snapshot SHA256 must be 64 lowercase hexadecimal characters")
    return digest


def _registry_key(generation_id: str, repo_root: Optional[str]):
    root = None if repo_root is None else os.path.normcase(str(Path(repo_root).resolve()))
    return root, generation_id


def register_generation_snapshot(generation_id: str, hashes: Mapping[str, str], *,
                                 repo_root: Optional[str] = None, owner: object = None) -> None:
    if not isinstance(generation_id, str) or not generation_id:
        raise ValueError("snapshot registration requires a non-empty generation id")
    if not isinstance(hashes, Mapping):
        raise TypeError("snapshot hashes must be a mapping")
    normalized = {
        _canonical_rel_path(path): _canonical_sha256(digest)
        for path, digest in hashes.items()
    }
    key = _registry_key(generation_id, repo_root)
    hash(owner)
    with _SNAPSHOT_LOCK:
        prior = _SNAPSHOTS.get(key)
        if prior is not None and prior.hashes != normalized:
            raise RuntimeError(
                f"generation {generation_id} was registered with conflicting snapshot hashes"
            )
        if prior is None:
            _SNAPSHOTS[key] = _SnapshotRegistration(dict(normalized), {owner})
        else:
            prior.owners.add(owner)


def clear_generation_snapshot(generation_id: str, *, repo_root: Optional[str] = None,
                              owner: object = None) -> None:
    if not isinstance(generation_id, str) or not generation_id:
        return
    key = _registry_key(generation_id, repo_root)
    with _SNAPSHOT_LOCK:
        registration = _SNAPSHOTS.get(key)
        if registration is not None:
            registration.owners.discard(owner)
            if not registration.owners:
                del _SNAPSHOTS[key]


def generation_snapshot_hashes(generation_id: str, *, repo_root: Optional[str] = None) -> Optional[Dict[str, str]]:
    key = _registry_key(generation_id, repo_root)
    with _SNAPSHOT_LOCK:
        value = _SNAPSHOTS.get(key)
        return dict(value.hashes) if value is not None else None


def _open_repo_file(repo_root: str, rel_path: str):
    rel_path = _canonical_rel_path(rel_path)
    if os.name == "posix":
        parts = PurePosixPath(rel_path).parts
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        directory = getattr(os, "O_DIRECTORY", 0)
        nonblock = getattr(os, "O_NONBLOCK", 0)
        if not nofollow or not directory or not nonblock:
            raise SourceAvailabilityError("snapshot-bound reads require O_NOFOLLOW/O_DIRECTORY/O_NONBLOCK")

        root_fd = os.open(repo_root, os.O_RDONLY | directory | nofollow)
        current_fd = root_fd
        try:
            root_info = os.fstat(root_fd)
            if not stat.S_ISDIR(root_info.st_mode):
                raise SourceAvailabilityError("repository root is not a directory")
            for part in parts[:-1]:
                next_fd = os.open(
                    part,
                    os.O_RDONLY | directory | nofollow,
                    dir_fd=current_fd,
                )
                if current_fd != root_fd:
                    os.close(current_fd)
                current_fd = next_fd
            # A snapshotted regular file may be replaced by a FIFO before parsing.
            # Do not block in open before the consumer can reject it with fstat.
            file_fd = os.open(parts[-1], os.O_RDONLY | nofollow | nonblock, dir_fd=current_fd)
        finally:
            if current_fd != root_fd:
                try:
                    os.close(current_fd)
                except OSError:
                    pass
            try:
                os.close(root_fd)
            except OSError:
                pass
        try:
            return os.fdopen(file_fd, "rb")
        except BaseException:
            # Ownership transfers only after the Python handle exists.
            try:
                os.close(file_fd)
            except OSError:
                pass
            raise
    else:
        root_path = Path(repo_root).resolve(strict=True)
        target_path = (root_path / Path(rel_path)).resolve(strict=True)
        if target_path != root_path and root_path not in target_path.parents:
            raise SourceAvailabilityError(f"Path traversal detected: {rel_path}")
        for p in (target_path, *target_path.parents):
            if p == root_path:
                break
            if p.is_symlink():
                raise SourceAvailabilityError(f"Repository path contains a symlink: {p}")
        return open(target_path, "rb")


def read_generation_source_bytes(repo_root: str, rel_path: str, generation_id: str) -> bytes:
    canonical_rel = _canonical_rel_path(rel_path)
    key = _registry_key(generation_id, repo_root)
    # Lookup exactly one immutable digest while preserving scoped-first ownership.
    # Explicit inspection callers still receive a defensive full-map copy.
    with _SNAPSHOT_LOCK:
        registration = _SNAPSHOTS.get(key)
        if registration is None:
            registration = _SNAPSHOTS.get((None, generation_id))
        expected: Optional[str] = None
        if registration is not None:
            expected = registration.hashes.get(canonical_rel)
            if expected is None:
                raise FreshnessBusyError(
                    f"Parser requested a path absent from the STAGING snapshot: {canonical_rel}"
                )

    try:
        with _open_repo_file(repo_root, canonical_rel) as handle:
            before = os.fstat(handle.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise SourceAvailabilityError(
                    f"Snapshotted repository source is not a regular file: {canonical_rel}"
                )
            if before.st_size > MAX_INDEX_SOURCE_FILE_BYTES:
                raise SourceLimitError(
                    f"Snapshotted repository source exceeds {MAX_INDEX_SOURCE_FILE_BYTES} bytes: "
                    f"{canonical_rel}"
                )
            raw = handle.read(MAX_INDEX_SOURCE_FILE_BYTES + 1)
            after = os.fstat(handle.fileno())
    except FileNotFoundError as exc:
        raise FreshnessBusyError(
            f"Snapshotted repository source disappeared before parsing: {canonical_rel}"
        ) from exc
    except FreshnessBusyError:
        raise
    except OSError as exc:
        raise SourceAvailabilityError(
            f"Failed to open snapshotted repository source safely: {canonical_rel}: {exc}"
        ) from exc

    if len(raw) > MAX_INDEX_SOURCE_FILE_BYTES:
        raise SourceLimitError(
            f"Snapshotted repository source grew beyond {MAX_INDEX_SOURCE_FILE_BYTES} bytes: "
            f"{canonical_rel}"
        )
    before_identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    after_identity = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if before_identity != after_identity or len(raw) != after.st_size:
        raise FreshnessBusyError(
            f"Repository source changed while being read for parsing: {canonical_rel}"
        )

    actual = hashlib.sha256(raw).hexdigest()
    if expected is not None and actual != expected:
        raise FreshnessBusyError(
            f"Repository source no longer matches STAGING snapshot during parsing: {canonical_rel}"
        )
    return raw


def read_generation_source_text(repo_root: str, rel_path: str, generation_id: str) -> str:
    raw = read_generation_source_bytes(repo_root, rel_path, generation_id)
    try:
        return raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise SourceValidationError(
            f"Snapshotted repository source is not strict UTF-8: {rel_path}"
        ) from exc
