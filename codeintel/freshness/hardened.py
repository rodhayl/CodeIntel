"""Descriptor-safe production generation locking and snapshot binding."""

from __future__ import annotations

from codeintel.compat import fcntl, safe_fchmod
import hashlib
import os
import re
import stat
import sys
from pathlib import Path
from typing import Dict, Optional

from codeintel.bounded_process import run_bounded_process
from codeintel.core.contracts import (
    GENERATION_RETENTION_COUNT,
    MAX_IGNORE_FINGERPRINT_FILE_BYTES,
)
from codeintel.core.models import Generation
from codeintel.core.security import FreshnessBusyError, is_symlink_or_reparse
from codeintel.core.tooling import resolve_tool_binary
from codeintel.freshness.barrier import StagingResult
from codeintel.freshness.errors import SourceAvailabilityError, SourceEnumerationError
from codeintel.freshness.production import ProductionGenerationBarrier
from codeintel.freshness.snapshot_binding import (
    clear_generation_snapshot,
    register_generation_snapshot,
)
from codeintel.storage.policy import DerivedStateValidationError

_SAFE_GENERATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_MAX_IGNORE_PATH_BYTES = 16 * 1024
_MAX_GIT_CONFIG_STDERR_BYTES = 4 * 1024


class HardenedProductionGenerationBarrier(ProductionGenerationBarrier):
    """Production barrier with fail-closed locks, sidecars and STAGING snapshot identity."""

    @staticmethod
    def _hash_bounded_regular_file(hasher, path: str) -> None:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_BINARY", 0)
        )
        try:
            fd = os.open(path, flags)
        except FileNotFoundError:
            return
        except OSError as exc:
            raise SourceAvailabilityError(
                f"ignore configuration cannot be opened safely: {path}: {exc}"
            ) from exc
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode):
                raise SourceAvailabilityError(
                    f"ignore configuration must be a regular file: {path}"
                )
            if before.st_size > MAX_IGNORE_FINGERPRINT_FILE_BYTES:
                raise SourceAvailabilityError(
                    "ignore configuration exceeds production byte limit "
                    f"({before.st_size} > {MAX_IGNORE_FINGERPRINT_FILE_BYTES}): {path}"
                )
            raw = os.read(fd, MAX_IGNORE_FINGERPRINT_FILE_BYTES + 1)
            after = os.fstat(fd)
            if len(raw) > MAX_IGNORE_FINGERPRINT_FILE_BYTES:
                raise SourceAvailabilityError(
                    f"ignore configuration grew beyond production byte limit: {path}"
                )
            before_identity = (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
            )
            after_identity = (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
            )
            if before_identity != after_identity or len(raw) != after.st_size:
                raise FreshnessBusyError(
                    f"ignore configuration changed while fingerprinting: {path}"
                )
            hasher.update(path.encode("utf-8", errors="surrogateescape"))
            hasher.update(b"\x00")
            hasher.update(raw)
            hasher.update(b"\x00")
        finally:
            os.close(fd)

    def _compute_ignore_fingerprint(self) -> str:
        """Hash effective root/info ignores, never disabled external core.excludesfile.

        Nested .gitignore changes are captured by full candidate/byte snapshots;
        production never uses an ignore-only or Git-status freshness shortcut.
        """
        hasher = hashlib.sha256()
        ignore_files = [os.path.join(self.repo_root, ".gitignore")]
        if os.path.exists(os.path.join(self.repo_root, ".git")):
            # In a linked worktree ``.git`` is a regular indirection file rather than a
            # directory. Ask Git for the effective info/exclude path instead of
            # constructing ``.git/info/exclude`` from the worktree pathname.
            git_binary = resolve_tool_binary("git")
            if git_binary is None:
                raise SourceEnumerationError("trusted Git executable is unavailable")
            try:
                git_path = run_bounded_process(
                    [git_binary, "rev-parse", "--git-path", "info/exclude"],
                    cwd=self.repo_root,
                    timeout_seconds=2.0,
                    stdout_limit=_MAX_IGNORE_PATH_BYTES + 1,
                    stderr_limit=_MAX_GIT_CONFIG_STDERR_BYTES,
                )
            except OSError as exc:
                raise SourceEnumerationError(
                    f"could not resolve Git info/exclude safely: {exc}"
                ) from exc
            if git_path.timed_out:
                raise SourceEnumerationError("git rev-parse --git-path info/exclude timed out")
            if git_path.observed_stdout_bytes > _MAX_IGNORE_PATH_BYTES:
                raise SourceEnumerationError("Git info/exclude path exceeds production byte limit")
            if git_path.returncode != 0:
                detail = git_path.stderr.decode("utf-8", errors="replace").strip()[-1000:]
                raise SourceEnumerationError(
                    f"git rev-parse --git-path info/exclude failed with exit code {git_path.returncode}"
                    + (f": {detail}" if detail else "")
                )
            try:
                raw_git_path = git_path.stdout.decode("utf-8", errors="strict").strip()
            except UnicodeDecodeError as exc:
                raise SourceEnumerationError("Git info/exclude path is not valid UTF-8") from exc
            if not raw_git_path:
                raise SourceEnumerationError("Git info/exclude path was empty")
            if "\x00" in raw_git_path or "\n" in raw_git_path or "\r" in raw_git_path:
                raise SourceEnumerationError("Git info/exclude path contains unsafe control characters")
            if not os.path.isabs(raw_git_path):
                raw_git_path = os.path.join(self.repo_root, raw_git_path)
            ignore_files.append(os.path.normpath(raw_git_path))

        for path in ignore_files:
            self._hash_bounded_regular_file(hasher, path)
        return hasher.hexdigest()

    def create_staging_generation(self) -> StagingResult:
        result = super().create_staging_generation()
        if result.reused_existing:
            return result
        if not hasattr(self, "_snapshot_binding_owner"):
            self._snapshot_binding_owner = object()
        if not hasattr(self, "_registered_snapshot_ids"):
            self._registered_snapshot_ids = set()
        try:
            register_generation_snapshot(
                result.generation.generation_id,
                result.current_hashes,
                repo_root=getattr(self, "repo_root", None), owner=self._snapshot_binding_owner,
            )
            self._registered_snapshot_ids.add(result.generation.generation_id)
        except BaseException:
            try:
                self.abort_staging_generation(result.generation)
            finally:
                self._clear_owned_snapshot(result.generation.generation_id)
            raise
        return result

    def _clear_owned_snapshot(self, generation_id: str) -> None:
        owner = getattr(self, "_snapshot_binding_owner", None)
        if owner is None:
            return
        clear_generation_snapshot(generation_id, repo_root=getattr(self, "repo_root", None), owner=owner)
        self._registered_snapshot_ids.discard(generation_id)

    def _clear_owned_snapshots(self) -> None:
        failures = []
        for generation_id in tuple(getattr(self, "_registered_snapshot_ids", ())):
            try:
                self._clear_owned_snapshot(generation_id)
            except BaseException as error:
                failures.append(error)
        if failures:
            primary = failures[0]
            for secondary in failures[1:]:
                primary.add_note(f"Owned snapshot cleanup also failed: {secondary}")
            raise primary

    @staticmethod
    def _release_lease_fd(fd: Optional[int]) -> None:
        if fd is None:
            return
        failure = None
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        except BaseException as error:
            failure = error
        try:
            os.close(fd)
        except OSError:
            pass
        except BaseException as cleanup_error:
            if failure is None:
                raise
            failure.add_note(f"Lease descriptor cleanup also failed: {cleanup_error}")
        if failure is not None:
            raise failure

    def _retain_transition_lease_fd(self, fd: Optional[int]) -> None:
        """Keep an additional lease locked while the authoritative active generation is unknown."""
        if fd is None:
            return
        retained = getattr(self, "_retained_transition_lease_fds", None)
        if retained is None:
            retained = []
            self._retained_transition_lease_fds = retained
        if fd not in retained:
            retained.append(fd)

    def _release_retained_transition_leases(self) -> None:
        with self._lock:
            retained = list(getattr(self, "_retained_transition_lease_fds", []) or [])
            self._retained_transition_lease_fds = []
        failures = []
        for fd in retained:
            try:
                self._release_lease_fd(fd)
            except BaseException as error:
                failures.append(error)
        if failures:
            primary = failures[0]
            for secondary in failures[1:]:
                primary.add_note(f"Retained lease cleanup also failed: {secondary}")
            raise primary

    def close(self) -> None:
        failure = None
        try:
            super().close()
        except BaseException as error:
            failure = error
        for cleanup in (self._release_retained_transition_leases, self._clear_owned_snapshots):
            try:
                cleanup()
            except BaseException as cleanup_error:
                if failure is None:
                    failure = cleanup_error
                else:
                    failure.add_note(f"Owned transition cleanup also failed: {cleanup_error}")
        if failure is not None:
            raise failure

    def commit_staging_generation(
        self, staging_gen: Generation, new_hashes: Dict[str, str]
    ) -> None:
        """Hold both old and prospective leases across the SQLite activation boundary."""
        generation_id = staging_gen.generation_id
        self._validate_offline_artifacts(generation_id)

        prepared_fd: Optional[int] = None
        old_fd: Optional[int] = None
        old_generation_id: Optional[str] = None
        try:
            prepared_fd = self._open_generation_lease_fd(generation_id)
            try:
                fcntl.flock(prepared_fd, fcntl.LOCK_SH)
            except Exception:
                self._release_lease_fd(prepared_fd)
                prepared_fd = None
                raise

            with self._lock:
                old_fd = self._active_lease_fd
                old_generation_id = self._leased_generation_id
                self._active_lease_fd = prepared_fd
                self._leased_generation_id = generation_id
                prepared_fd = None

            try:
                # ProductionGenerationBarrier rejects legacy sidecars, then the
                # base commit performs snapshot verification + DB activation. Because
                # the prospective lease is already installed, its internal lease switch
                # is a no-op and cannot introduce a new post-activation failure point.
                result = super().commit_staging_generation(staging_gen, new_hashes)
            except BaseException:
                authority_known = True
                try:
                    active = self.gen_store.get_active_generation(self.repo_id)
                except BaseException:
                    authority_known = False
                    active = None
                with self._lock:
                    installed_new_fd = self._active_lease_fd
                    if not authority_known:
                        # We cannot prove which side of SQLite activation committed.
                        # Keep the prospective lease installed AND retain the previous
                        # lease so GC cannot delete either candidate until close/restart.
                        self._retain_transition_lease_fd(old_fd)
                        old_fd = None
                    elif active is not None and active.generation_id == generation_id:
                        self._active_generation = active
                        # SQLite crossed the activation boundary: keep the new lease and
                        # retire the old lease even though the caller must still observe
                        # the original commit exception.
                        self._release_lease_fd(old_fd)
                        old_fd = None
                    else:
                        # Activation is authoritatively still old/non-new: restore the
                        # previous lease and release the prospective one.
                        self._active_lease_fd = old_fd
                        self._leased_generation_id = old_generation_id
                        old_fd = None
                        self._release_lease_fd(installed_new_fd)
                raise
            else:
                self._release_lease_fd(old_fd)
                old_fd = None
                return result
        finally:
            self._release_lease_fd(prepared_fd)
            if old_fd is not None:
                with self._lock:
                    if self._active_lease_fd != old_fd:
                        self._release_lease_fd(old_fd)
                old_fd = None
            self._clear_owned_snapshot(generation_id)

    def abort_staging_generation(
        self, staging_gen: Optional[Generation] = None
    ) -> None:
        """Abort only state proven non-active by the authoritative generation store."""
        with self._lock:
            try:
                if staging_gen is None:
                    return
                try:
                    active = self.gen_store.get_active_generation(self.repo_id)
                except Exception:
                    # Authority is unavailable: fail closed by retaining derived staging
                    # state for later GC rather than risking deletion of an active gen.
                    return
                if active is not None and active.generation_id == staging_gen.generation_id:
                    self._active_generation = active
                    try:
                        self._switch_generation_lease(active.generation_id)
                    except Exception:
                        pass
                    return
                try:
                    self.gen_store.delete_generation_data(staging_gen.generation_id)
                except Exception:
                    pass
                try:
                    self._remove_generation_sidecar(staging_gen.generation_id)
                except Exception:
                    pass
            finally:
                if staging_gen is not None:
                    self._clear_owned_snapshot(staging_gen.generation_id)
                self.is_rebuilding = False
                try:
                    self.release_rebuild_lock()
                except Exception:
                    pass

    def _open_state_root_fd(self) -> int:
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        directory = getattr(os, "O_DIRECTORY", 0)
        if not nofollow or not directory:
            raise RuntimeError("production state locking requires O_NOFOLLOW/O_DIRECTORY")
        try:
            fd = os.open(self.state_dir, os.O_RDONLY | directory | nofollow)
        except OSError as exc:
            raise DerivedStateValidationError(f"trusted state directory cannot be opened safely: {exc}") from exc
        try:
            info = os.fstat(fd)
            if not stat.S_ISDIR(info.st_mode):
                raise DerivedStateValidationError("trusted state path is not a directory")
            return fd
        except BaseException as error:
            try:
                os.close(fd)
            except BaseException as cleanup_error:
                error.add_note(f"State descriptor cleanup also failed: {cleanup_error}")
            raise

    def acquire_rebuild_lock(self) -> None:
        if self._rebuild_lock_file is not None:
            return
        if os.name == "posix":
            state_fd = self._open_state_root_fd()
            lock_fd = None
            try:
                flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
                try:
                    lock_fd = os.open("rebuild.lock", flags, 0o600, dir_fd=state_fd)
                except OSError as exc:
                    raise DerivedStateValidationError(
                        f"production rebuild lock cannot be opened safely: {exc}"
                    ) from exc
                info = os.fstat(lock_fd)
                if not stat.S_ISREG(info.st_mode):
                    raise DerivedStateValidationError("production rebuild lock must be a regular file")
                safe_fchmod(lock_fd, 0o600)
                fcntl.flock(lock_fd, fcntl.LOCK_EX)
                self._rebuild_lock_file = lock_fd
                lock_fd = None
            finally:
                if lock_fd is not None:
                    try:
                        os.close(lock_fd)
                    except OSError:
                        pass
                os.close(state_fd)
        else:
            lock_path = Path(self.state_dir) / "rebuild.lock"
            if is_symlink_or_reparse(lock_path):
                raise DerivedStateValidationError("production rebuild lock must not be a symlink")
            lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR | getattr(os, "O_BINARY", 0), 0o600)
            info = os.fstat(lock_fd)
            if not stat.S_ISREG(info.st_mode):
                os.close(lock_fd)
                raise DerivedStateValidationError("production rebuild lock must be a regular file")
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            self._rebuild_lock_file = lock_fd

    def _open_lease_dir_fd(self) -> int:
        state_fd = self._open_state_root_fd()
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        directory = getattr(os, "O_DIRECTORY", 0)
        try:
            try:
                os.mkdir("generation_leases", 0o700, dir_fd=state_fd)
            except FileExistsError:
                pass
            try:
                lease_dir_fd = os.open(
                    "generation_leases",
                    os.O_RDONLY | directory | nofollow,
                    dir_fd=state_fd,
                )
            except OSError as exc:
                raise DerivedStateValidationError(
                    f"generation lease directory cannot be opened safely: {exc}"
                ) from exc
        finally:
            os.close(state_fd)
        try:
            info = os.fstat(lease_dir_fd)
            if not stat.S_ISDIR(info.st_mode):
                raise DerivedStateValidationError("generation lease path must be a directory")
            safe_fchmod(lease_dir_fd, 0o700)
            return lease_dir_fd
        except BaseException as error:
            try:
                os.close(lease_dir_fd)
            except BaseException as cleanup_error:
                error.add_note(f"Lease directory cleanup also failed: {cleanup_error}")
            raise

    @staticmethod
    def _lease_filename(generation_id: str) -> str:
        if not isinstance(generation_id, str) or not generation_id:
            raise ValueError("generation lease requires a non-empty generation id")
        digest = hashlib.sha256(generation_id.encode("utf-8")).hexdigest()[:32]
        return f"{digest}.lock"

    def _open_generation_lease_fd(self, generation_id: str) -> int:
        if os.name == "posix":
            lease_dir_fd = self._open_lease_dir_fd()
            fd = None
            try:
                flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
                try:
                    fd = os.open(
                        self._lease_filename(generation_id),
                        flags,
                        0o600,
                        dir_fd=lease_dir_fd,
                    )
                except OSError as exc:
                    raise DerivedStateValidationError(
                        f"generation lease cannot be opened safely: {exc}"
                    ) from exc
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode):
                    raise DerivedStateValidationError("generation lease must be a regular file")
                safe_fchmod(fd, 0o600)
                result = fd
                fd = None
                return result
            finally:
                if fd is not None:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
                os.close(lease_dir_fd)
        else:
            lease_dir = Path(self.state_dir) / "generation_leases"
            if is_symlink_or_reparse(lease_dir):
                raise DerivedStateValidationError("generation lease directory must not be a symlink")
            lease_dir.mkdir(parents=True, exist_ok=True)
            lease_path = lease_dir / self._lease_filename(generation_id)
            if is_symlink_or_reparse(lease_path):
                raise DerivedStateValidationError("generation lease must not be a symlink")
            fd = os.open(str(lease_path), os.O_CREAT | os.O_RDWR | getattr(os, "O_BINARY", 0), 0o600)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                os.close(fd)
                raise DerivedStateValidationError("generation lease must be a regular file")
            return fd

    def _generation_lease_path(self, generation_id: str) -> str:
        if sys.platform == "win32":
            lease_dir = Path(self.state_dir) / "generation_leases"
            if is_symlink_or_reparse(lease_dir):
                raise DerivedStateValidationError("generation lease directory must not be a symlink")
            return str(lease_dir / self._lease_filename(generation_id))
        lease_dir_fd = self._open_lease_dir_fd()
        os.close(lease_dir_fd)
        return os.path.join(
            self.state_dir, "generation_leases", self._lease_filename(generation_id)
        )

    def _switch_generation_lease(self, generation_id: Optional[str]) -> None:
        if generation_id == self._leased_generation_id:
            return
        new_fd: Optional[int] = None
        if generation_id:
            new_fd = self._open_generation_lease_fd(generation_id)
            try:
                fcntl.flock(new_fd, fcntl.LOCK_SH)
            except BaseException as error:
                try:
                    os.close(new_fd)
                except BaseException as cleanup_error:
                    error.add_note(f"Generation lease cleanup also failed: {cleanup_error}")
                raise
        old_fd = self._active_lease_fd
        self._active_lease_fd = new_fd
        self._leased_generation_id = generation_id
        if old_fd is not None:
            try:
                fcntl.flock(old_fd, fcntl.LOCK_UN)
            finally:
                os.close(old_fd)

    def _remove_generation_sidecar(self, generation_id: str) -> None:
        if not isinstance(generation_id, str) or not _SAFE_GENERATION_ID.fullmatch(generation_id):
            raise ValueError("generation_id contains unsafe sidecar characters")
        if sys.platform == "win32":
            sidecar_dir = Path(self.state_dir) / ".codeintel_vectors"
            if not sidecar_dir.exists():
                return
            if is_symlink_or_reparse(sidecar_dir) or not sidecar_dir.is_dir():
                raise RuntimeError("vector sidecar path must be a directory")
            target = sidecar_dir / f"vectors_{generation_id}.npz"
            if not target.exists():
                return
            if is_symlink_or_reparse(target) or not target.is_file():
                raise RuntimeError("generation sidecar target must be a regular file")
            try:
                target.unlink()
            except FileNotFoundError:
                pass
            return
        state_fd = self._open_state_root_fd()
        sidecar_fd: Optional[int] = None
        try:
            flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
            try:
                sidecar_fd = os.open(
                    ".codeintel_vectors",
                    flags,
                    dir_fd=state_fd,
                )
            except FileNotFoundError:
                return
            except OSError as exc:
                raise RuntimeError(
                    f"vector sidecar directory cannot be opened safely: {exc}"
                ) from exc
            if not stat.S_ISDIR(os.fstat(sidecar_fd).st_mode):
                raise RuntimeError("vector sidecar path must be a directory")
            name = f"vectors_{generation_id}.npz"
            try:
                info = os.stat(name, dir_fd=sidecar_fd, follow_symlinks=False)
            except FileNotFoundError:
                return
            if not stat.S_ISREG(info.st_mode):
                raise RuntimeError("generation sidecar target must be a regular file")
            os.unlink(name, dir_fd=sidecar_fd)
            os.fsync(sidecar_fd)
        finally:
            if sidecar_fd is not None:
                os.close(sidecar_fd)
            os.close(state_fd)

    def _gc_generations(self, retain: int = GENERATION_RETENTION_COUNT) -> Dict[str, object]:
        report: Dict[str, object] = {"deleted": [], "leased": [], "errors": []}
        try:
            generations = list(self.gen_store.list_generations(self.repo_id))
            active = self.gen_store.get_active_generation(self.repo_id)
            active_id = active.generation_id if active else None
            committed = [
                gen for gen in generations
                if (getattr(gen, "metadata", {}) or {}).get("lifecycle_state") != "STAGING"
            ]
            keep_ids = {gen.generation_id for gen in committed[:max(1, retain)]}
            if active_id:
                keep_ids.add(active_id)

            for gen in generations:
                gen_id = gen.generation_id
                meta = getattr(gen, "metadata", {}) or {}
                stale_staging = meta.get("lifecycle_state") == "STAGING" and gen_id != active_id
                if gen_id in keep_ids and not stale_staging:
                    continue
                lease_fd: Optional[int] = None
                try:
                    lease_fd = self._open_generation_lease_fd(gen_id)
                    try:
                        fcntl.flock(lease_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        report["leased"].append(gen_id)
                        continue
                    self.gen_store.delete_generation_data(gen_id)
                    self._remove_generation_sidecar(gen_id)
                    report["deleted"].append(gen_id)
                except Exception as exc:
                    report["errors"].append(f"{gen_id}:{type(exc).__name__}:{exc}")
                finally:
                    if lease_fd is not None:
                        self._release_lease_fd(lease_fd)
        except Exception as exc:
            report["errors"].append(f"gc:{type(exc).__name__}:{exc}")
        self.last_gc_report = report
        return report


__all__ = ["HardenedProductionGenerationBarrier"]
