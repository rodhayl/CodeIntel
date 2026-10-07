"""Safe full-snapshot generations for the offline lab.

Legacy sidecar validation/cleanup is retained for stored-state compatibility;
no dense provider or incremental relation-reuse path is delivered by the CLI.
"""

import hashlib
import os
import re
import stat
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from codeintel.bounded_process import run_bounded_process
from codeintel.core.contracts import (
    MAX_GIT_CANDIDATE_OUTPUT_BYTES,
    MAX_INDEX_CANDIDATE_FILES,
    MAX_INDEX_CANDIDATE_DIRECTORIES,
    MAX_INDEX_SOURCE_FILE_BYTES,
)
from codeintel.core.models import Generation
from codeintel.core.security import FreshnessBusyError, RepoJail, SecurityException, is_binary_content, is_symlink_or_reparse
from codeintel.core.tooling import resolve_tool_binary
from codeintel.freshness.barrier import GenerationBarrier, StagingResult
from codeintel.freshness.errors import (
    SourceAvailabilityError, SourceEnumerationError, SourceLimitError, SourceValidationError,
    SourcePathPolicyError,
)

_CODE_SOURCE_SUFFIXES = {".py", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"}
_MAX_GIT_STDERR_BYTES = 8 * 1024
_GIT_ENUMERATION_TIMEOUT_SECONDS = 5.0


class _RecoverableArtifactError(RuntimeError):
    """A recognized legacy/duplicate artifact can be replaced by a new generation."""


def _open_canonical_repo_file(repo_root: str, rel_path: str):
    """Open one canonical repository file without following any path-component symlink.

    ``rel_path`` must already be the jail-resolved canonical path. Walking from an
    open repository-root descriptor closes the containment TOCTOU that exists when a
    pathname is resolved/stat'ed and then opened in a second filesystem lookup. The
    final component is opened non-blocking so a named pipe/device cannot hang the
    scanner before the descriptor is rejected as non-regular by ``fstat``.
    """
    parts = Path(rel_path).parts
    if not parts or any(part in ("", ".", "..") for part in parts):
        raise ValueError("canonical repository path contains unsafe components")
    if os.name == "posix":
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        directory = getattr(os, "O_DIRECTORY", 0)
        nonblock = getattr(os, "O_NONBLOCK", 0)
        if not nofollow or not directory or not nonblock:
            raise SourceAvailabilityError(
                "production repository scanning requires O_NOFOLLOW/O_DIRECTORY/O_NONBLOCK"
            )

        root_fd = os.open(repo_root, os.O_RDONLY | directory | nofollow)
        current_fd = root_fd
        try:
            for part in parts[:-1]:
                next_fd = os.open(
                    part,
                    os.O_RDONLY | directory | nofollow,
                    dir_fd=current_fd,
                )
                if current_fd != root_fd:
                    os.close(current_fd)
                current_fd = next_fd
            file_fd = os.open(
                parts[-1], os.O_RDONLY | nofollow | nonblock, dir_fd=current_fd
            )
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
            raise ValueError(f"Path traversal detected: {rel_path}")
        for p in (target_path, *target_path.parents):
            if p == root_path:
                break
            if is_symlink_or_reparse(p):
                raise SourceAvailabilityError(f"Repository path contains a symlink or reparse point: {p}")
        return open(target_path, "rb")


def _bounded_git_candidate_paths(repo_root: str) -> List[str]:
    """Enumerate candidates through the shared bounded process ownership boundary."""
    git_binary = resolve_tool_binary("git")
    if git_binary is None:
        raise SourceEnumerationError("trusted Git executable is unavailable")
    result = run_bounded_process(
        [
            git_binary,
            "-c", "core.fsmonitor=false",
            "-c", f"core.excludesfile={os.devnull}",
            "--no-optional-locks", "ls-files", "--cached", "--others",
            "--exclude-standard", "-z",
        ],
        cwd=repo_root,
        timeout_seconds=_GIT_ENUMERATION_TIMEOUT_SECONDS,
        stdout_limit=MAX_GIT_CANDIDATE_OUTPUT_BYTES,
        stderr_limit=_MAX_GIT_STDERR_BYTES,
    )
    if result.timed_out:
        raise SourceEnumerationError("Git candidate enumeration timed out")
    if result.observed_stdout_bytes > MAX_GIT_CANDIDATE_OUTPUT_BYTES:
        raise SourceEnumerationError(
            "Git candidate enumeration exceeded production output byte limit "
            f"({MAX_GIT_CANDIDATE_OUTPUT_BYTES})"
        )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace")[-1024:]
        raise SourceEnumerationError(
            f"Git candidate enumeration failed with exit code {result.returncode}: {detail}"
        )
    if result.stdout and not result.stdout.endswith(b"\x00"):
        raise SourceEnumerationError("Git candidate enumeration returned an incomplete NUL-delimited path")

    candidate_paths: List[str] = []
    for entry in result.stdout.split(b"\x00"):
        if not entry:
            continue
        if len(candidate_paths) >= MAX_INDEX_CANDIDATE_FILES:
            raise SourceEnumerationError(
                "Repository candidate enumeration exceeds production file-count limit "
                f"({MAX_INDEX_CANDIDATE_FILES})"
            )
        # Git emits repository-relative paths using '/' on every platform.
        # A POSIX backslash belongs to the filename and must reach RepoJail
        # unchanged, where it is rejected instead of aliasing a different file.
        candidate_paths.append(entry.decode("utf-8", errors="surrogateescape"))
    return candidate_paths


def _bounded_filesystem_candidate_paths(repo_root: str, jail) -> List[str]:
    """Bound file candidates and total child-directory admissions separately."""
    candidate_paths: List[str] = []
    stack: List[Tuple[str, str]] = [("", repo_root)]
    admitted_directories = 0
    while stack:
        rel_dir, absolute_dir = stack.pop()
        try:
            with os.scandir(absolute_dir) as entries:
                for entry in entries:
                    rel_path = (
                        entry.name
                        if not rel_dir
                        else os.path.join(rel_dir, entry.name)
                    )
                    # Convert only native separators. On POSIX, replacing every
                    # backslash would silently merge distinct physical paths.
                    rel_path = Path(os.path.normpath(rel_path)).as_posix()
                    try:
                        is_directory = entry.is_dir(follow_symlinks=False)
                    except OSError as exc:
                        raise SourceEnumerationError(
                            f"Repository candidate type could not be inspected: {rel_path}: {exc}"
                        ) from exc
                    if is_directory:
                        if jail.is_path_allowed(rel_path):
                            if admitted_directories >= MAX_INDEX_CANDIDATE_DIRECTORIES:
                                raise SourceEnumerationError(
                                    "Repository candidate enumeration exceeds production directory-count limit "
                                    f"({MAX_INDEX_CANDIDATE_DIRECTORIES})"
                                )
                            admitted_directories += 1
                            stack.append((rel_path, entry.path))
                        continue
                    if len(candidate_paths) >= MAX_INDEX_CANDIDATE_FILES:
                        raise SourceEnumerationError(
                            "Repository candidate enumeration exceeds production file-count limit "
                            f"({MAX_INDEX_CANDIDATE_FILES})"
                        )
                    candidate_paths.append(rel_path)
        except RuntimeError:
            raise
        except OSError as exc:
            raise SourceEnumerationError(
                f"Repository directory could not be enumerated safely: {absolute_dir}: {exc}"
            ) from exc
    return candidate_paths


class ProductionGenerationBarrier(GenerationBarrier):
    """Production barrier: safe sidecars, explicit leases, conservative rebuilds."""

    def _get_git_head(self) -> Optional[str]:
        """Read optional HEAD identity through the same trusted, bounded tool boundary."""
        if not os.path.exists(os.path.join(self.repo_root, ".git")):
            return None
        git_binary = resolve_tool_binary("git")
        if git_binary is None:
            return None
        try:
            result = run_bounded_process(
                [git_binary, "--no-optional-locks", "rev-parse", "HEAD"],
                cwd=self.repo_root,
                timeout_seconds=2.0,
                stdout_limit=256,
                stderr_limit=1024,
            )
            if result.timed_out or result.returncode != 0 or result.observed_stdout_bytes > 256:
                return None
            head = result.stdout.decode("ascii", errors="strict").strip()
            return head if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", head) else None
        except (OSError, UnicodeError):
            # Unborn/non-Git repositories have no optional HEAD identity. Actual
            # candidate enumeration still fails closed if Git cannot inspect them.
            return None

    def _validate_offline_artifacts(self, generation_id: str) -> None:
        """Reject legacy sidecars before reusing or activating an offline generation."""
        if not isinstance(generation_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", generation_id):
            raise ValueError("generation_id contains unsafe sidecar characters")
        getter = getattr(self.gen_store, "get_all_chunk_ids", None)
        if callable(getter):
            db_ids = list(getter(generation_id))
            if len(db_ids) != len(set(db_ids)):
                raise _RecoverableArtifactError(
                    f"Production activation found duplicate SQLite chunk ids for {generation_id}"
                )
        sidecar_path = Path(self.state_dir) / ".codeintel_vectors" / f"vectors_{generation_id}.npz"
        if os.path.lexists(sidecar_path):
            raise _RecoverableArtifactError("Offline generation unexpectedly has a legacy vector sidecar")

    def _active_artifact_repair_reason(self) -> Optional[str]:
        """Return a reason when the current active generation cannot be safely reused.

        Snapshot/build equality alone is insufficient for a reused-existing fast path:
        persisted derived artifacts may have been removed or corrupted after activation.
        A broken active artifact is repaired by a new immutable generation; the old
        active generation remains untouched until the repair passes precommit.
        """
        active = self.active_generation
        if active is None:
            return None
        try:
            self._validate_offline_artifacts(active.generation_id)
            return None
        except _RecoverableArtifactError as exc:
            reason = f"{type(exc).__name__}: {exc}"
            return reason

    def commit_staging_generation(self, staging_gen: Generation, new_hashes: Dict[str, str]) -> None:
        self._validate_offline_artifacts(staging_gen.generation_id)
        return super().commit_staging_generation(staging_gen, new_hashes)

    def scan_worktree(self) -> Dict[str, str]:
        return self.scan_repository(self.repo_root, self.jail)

    @staticmethod
    def scan_repository(
        repo_root: str,
        jail: RepoJail,
    ) -> Dict[str, str]:
        """Enumerate a deterministic, bounded set of regular UTF-8 repository files.

        Production canonicalizes every candidate through RepoJail and then opens that
        canonical path from a repository-root descriptor with O_NOFOLLOW on every
        component. This prevents a symlink swap between containment validation and the
        actual read. The opened descriptor is also checked for identity stability while
        bytes are captured.
        """
        current_hashes: Dict[str, str] = {}
        if os.path.exists(os.path.join(repo_root, ".git")):
            candidate_paths = _bounded_git_candidate_paths(repo_root)
        else:
            candidate_paths = _bounded_filesystem_candidate_paths(repo_root, jail)

        for rel_path in sorted(set(candidate_paths)):
            if "\\" in rel_path:
                # A literal POSIX backslash is not a path separator. Do not
                # normalize it into an alias, or misdiagnose it as a symlink.
                raise SourcePathPolicyError("Repository filenames containing a backslash are not canonical")
            try:
                canonical_rel = jail.safe_relpath(rel_path)
            except SecurityException as exc:
                raise SourceAvailabilityError(
                    f"Repository candidate escaped the production jail: {rel_path}: {exc}"
                ) from exc
            if not jail.is_path_allowed(canonical_rel):
                continue

            try:
                rel_path.encode('utf-8')  # The original alias is stored in snapshot identity.
                canonical_rel.encode('utf-8')
            except UnicodeEncodeError as exc:
                raise SourceAvailabilityError('Repository source paths must be valid UTF-8 text') from exc

            try:
                with _open_canonical_repo_file(repo_root, canonical_rel) as handle:
                    fd_pre = os.fstat(handle.fileno())
                    if not stat.S_ISREG(fd_pre.st_mode):
                        raise SourceAvailabilityError(
                            f"Eligible repository candidate is not a regular file: {rel_path}"
                        )
                    if fd_pre.st_size > MAX_INDEX_SOURCE_FILE_BYTES:
                        raise SourceLimitError(
                            f"Eligible repository source exceeds production file limit "
                            f"({fd_pre.st_size} > {MAX_INDEX_SOURCE_FILE_BYTES} bytes): {rel_path}"
                        )
                    content = handle.read(MAX_INDEX_SOURCE_FILE_BYTES + 1)
                    fd_post = os.fstat(handle.fileno())
            except FileNotFoundError:
                continue
            except RuntimeError:
                raise
            except OSError as exc:
                raise SourceAvailabilityError(
                    f"Eligible repository source could not be read without following symlinks: "
                    f"{rel_path}: {exc}"
                ) from exc

            if len(content) > MAX_INDEX_SOURCE_FILE_BYTES:
                raise SourceLimitError(
                    f"Eligible repository source grew beyond production file limit while reading: {rel_path}"
                )
            identity_before = (
                getattr(fd_pre, "st_dev", 0),
                fd_pre.st_ino,
                fd_pre.st_size,
                fd_pre.st_mtime_ns,
            )
            identity_after = (
                getattr(fd_post, "st_dev", 0),
                fd_post.st_ino,
                fd_post.st_size,
                fd_post.st_mtime_ns,
            )
            if identity_before != identity_after or len(content) != fd_post.st_size:
                raise FreshnessBusyError(
                    f"Repository source changed while being read for snapshot: {rel_path}"
                )

            if is_binary_content(content):
                if Path(rel_path).suffix.lower() in _CODE_SOURCE_SUFFIXES:
                    raise SourceValidationError(
                        f"Supported code source must be regular UTF-8 text, not binary/invalid UTF-8: {rel_path}"
                    )
                continue

            current_hashes[rel_path] = hashlib.sha256(content).hexdigest()
        return current_hashes

    def check_freshness(self) -> Tuple[bool, List[str], List[str], List[str]]:
        """Verify production freshness from repository bytes, never Git status alone.

        Git index flags such as assume-unchanged/skip-worktree can hide on-disk source
        modifications from ``git status``. Production therefore pays the bounded
        snapshot/hash cost before serving an active generation.
        """
        with self._lock:
            latest = self.gen_store.get_active_generation(self.repo_id)
            if latest:
                self._active_generation = latest
            active = self._active_generation
            current, head, ignore = self._stable_snapshot()
            if active is None:
                return False, sorted(current), [], []
            meta = getattr(active, "metadata", {}) or {}
            if meta.get("build_fingerprint") != self.build_fingerprint:
                return False, sorted(current), [], []

            prior = self.file_hashes
            snapshot_matches = self._snapshot_hash(current) == active.snapshot_hash
            if snapshot_matches:
                self.file_hashes = dict(current)
                self.active_git_head = head
                self.ignore_fingerprint = ignore
                return True, [], [], []

            if prior is None:
                return False, sorted(current), [], []
            current_keys, active_keys = set(current), set(prior)
            added = sorted(current_keys - active_keys)
            deleted = sorted(active_keys - current_keys)
            modified = sorted(
                key for key in current_keys & active_keys if current[key] != prior[key]
            )
            if not (added or deleted or modified):
                modified = sorted(current_keys)
            return False, added, modified, deleted

    def create_staging_generation(self) -> StagingResult:
        """Force full semantic rebuild and repair invalid active artifacts immutably."""
        repair_reason = self._active_artifact_repair_reason()
        if repair_reason is not None:
            self.file_hashes = None

        result = super().create_staging_generation()
        if result.reused_existing:
            if repair_reason is not None:
                self.abort_staging_generation(result.generation)
                raise RuntimeError(
                    "Production artifact repair unexpectedly reused the invalid active generation"
                )
            return result

        try:
            metadata: Dict[str, object] = {}
            if repair_reason is not None:
                metadata.update(
                    {
                        "artifact_repair_required": True,
                        "artifact_repair_source_generation": (
                            self.active_generation.generation_id if self.active_generation else None
                        ),
                        "artifact_repair_reason": repair_reason[:1000],
                    }
                )

            updater = getattr(self.gen_store, "update_generation_metadata", None)
            if metadata and callable(updater):
                updater(result.generation.generation_id, metadata)

            return result
        except BaseException as error:
            try:
                self.abort_staging_generation(result.generation)
            except BaseException as cleanup_error:
                error.add_note(
                    "Staging metadata cleanup also failed: "
                    f"{type(cleanup_error).__name__}: {cleanup_error}"
                )
            raise

    def close(self) -> None:
        with self._lock:
            failures = []
            # This override owns locks only; the service owns store closure.
            # A failed rebuild unlock must not strand its independent lease.
            for operation in (self.release_rebuild_lock, lambda: self._switch_generation_lease(None)):
                try:
                    operation()
                except BaseException as error:
                    failures.append(error)
            if failures:
                primary = failures[0]
                for secondary in failures[1:]:
                    primary.add_note(f"Production barrier cleanup also failed: {secondary}")
                raise primary
