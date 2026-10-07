"""Derived-state lifecycle cleanup that is safe across CodeIntel processes."""

from codeintel.compat import fcntl, safe_fchmod
import os
import re
import stat
from pathlib import Path
from typing import Dict, Iterable

from codeintel.core.security import is_symlink_or_reparse

_SAFE_GENERATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _open_real_state_dir(state: Path) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(state, flags)
    try:
        if not stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("state directory descriptor is not a directory")
        return fd
    except BaseException:
        os.close(fd)
        raise


def _open_rebuild_lock(state_fd: int) -> int:
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open("rebuild.lock", flags, 0o600, dir_fd=state_fd)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("rebuild lock must be a regular file")
        return fd
    except BaseException:
        os.close(fd)
        raise


def _open_sidecar_dir(state_fd: int) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(".codeintel_vectors", flags, dir_fd=state_fd)
    try:
        if not stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("sidecar descriptor is not a directory")
        return fd
    except BaseException:
        os.close(fd)
        raise


def cleanup_orphan_vector_artifacts(state_dir: str, valid_generation_ids: Iterable[str]) -> Dict[str, object]:
    """Clean only CodeIntel-owned vector artifacts from one locked directory descriptor."""
    state = Path(state_dir)
    report: Dict[str, object] = {
        "orphan_sidecars_deleted": [],
        "temp_vector_files_deleted": [],
        "errors": [],
        "skipped_busy": False,
        "unsafe_state_dir": False,
        "unsafe_sidecar_dir": False,
    }
    if os.path.lexists(state) and os.path.islink(state):
        report["unsafe_state_dir"] = True
        report["errors"].append("state_dir:symlink_refused")
        return report
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not state.is_dir():
        report["unsafe_state_dir"] = True
        report["errors"].append("state_dir:not_directory")
        return report

    if os.name != "posix":
        lock_path = state / "rebuild.lock"
        try:
            lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR | getattr(os, "O_BINARY", 0), 0o600)
        except OSError as exc:
            report["unsafe_state_dir"] = True
            report["errors"].append(f"rebuild_lock:unsafe:{type(exc).__name__}:{exc}")
            return report
        try:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                report["skipped_busy"] = True
                return report

            sidecar_dir = state / ".codeintel_vectors"
            if not sidecar_dir.exists():
                return report
            if is_symlink_or_reparse(sidecar_dir) or not sidecar_dir.is_dir():
                report["unsafe_sidecar_dir"] = True
                report["errors"].append("sidecar_dir:not_directory")
                return report

            valid = set()
            for generation_id in valid_generation_ids:
                value = str(generation_id)
                if _SAFE_GENERATION_ID.fullmatch(value):
                    valid.add(value)
                else:
                    report["errors"].append(f"invalid_generation_id:{value!r}")

            try:
                names = sorted(os.listdir(str(sidecar_dir)))
            except OSError as exc:
                report["errors"].append(f"scan:{type(exc).__name__}:{exc}")
                return report

            for name in names:
                try:
                    if not isinstance(name, str) or not name or "/" in name or "\\" in name or "\x00" in name:
                        report["errors"].append(f"unsafe_entry_name:{name!r}")
                        continue
                    entry_path = sidecar_dir / name
                    if is_symlink_or_reparse(entry_path) or not entry_path.is_file():
                        continue
                    if name.startswith(".vectors_") and name.endswith(".tmp"):
                        entry_path.unlink()
                        report["temp_vector_files_deleted"].append(name)
                        continue
                    if not (name.startswith("vectors_") and name.endswith(".npz")):
                        continue
                    generation_id = name[len("vectors_"):-len(".npz")]
                    if not _SAFE_GENERATION_ID.fullmatch(generation_id):
                        report["errors"].append(f"unsafe_sidecar_name:{name}")
                        continue
                    if generation_id not in valid:
                        entry_path.unlink()
                        report["orphan_sidecars_deleted"].append(name)
                except FileNotFoundError:
                    continue
                except OSError as exc:
                    report["errors"].append(f"{name}:{type(exc).__name__}:{exc}")
            return report
        finally:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(lock_fd)

    try:
        state_fd = _open_real_state_dir(state)
    except OSError as exc:
        report["unsafe_state_dir"] = True
        report["errors"].append(f"state_dir:descriptor_refused:{type(exc).__name__}:{exc}")
        return report
    try:
        try:
            lock_fd = _open_rebuild_lock(state_fd)
        except OSError as exc:
            report["unsafe_state_dir"] = True
            report["errors"].append(f"rebuild_lock:unsafe:{type(exc).__name__}:{exc}")
            return report
        try:
            safe_fchmod(lock_fd, 0o600)
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                report["skipped_busy"] = True
                return report

            try:
                sidecar_fd = _open_sidecar_dir(state_fd)
            except FileNotFoundError:
                return report
            except OSError as exc:
                report["unsafe_sidecar_dir"] = True
                report["errors"].append(
                    f"sidecar_dir:descriptor_refused:{type(exc).__name__}:{exc}"
                )
                return report

            try:
                valid = set()
                for generation_id in valid_generation_ids:
                    value = str(generation_id)
                    if _SAFE_GENERATION_ID.fullmatch(value):
                        valid.add(value)
                    else:
                        report["errors"].append(f"invalid_generation_id:{value!r}")

                try:
                    names = sorted(os.listdir(sidecar_fd))
                except OSError as exc:
                    report["errors"].append(f"scan:{type(exc).__name__}:{exc}")
                    return report

                deleted_any = False
                for name in names:
                    try:
                        if not isinstance(name, str) or not name or "/" in name or "\x00" in name:
                            report["errors"].append(f"unsafe_entry_name:{name!r}")
                            continue
                        entry_stat = os.stat(name, dir_fd=sidecar_fd, follow_symlinks=False)
                        if stat.S_ISLNK(entry_stat.st_mode) or not stat.S_ISREG(entry_stat.st_mode):
                            continue
                        if name.startswith(".vectors_") and name.endswith(".tmp"):
                            os.unlink(name, dir_fd=sidecar_fd)
                            report["temp_vector_files_deleted"].append(name)
                            deleted_any = True
                            continue
                        if not (name.startswith("vectors_") and name.endswith(".npz")):
                            continue
                        generation_id = name[len("vectors_"):-len(".npz")]
                        if not _SAFE_GENERATION_ID.fullmatch(generation_id):
                            report["errors"].append(f"unsafe_sidecar_name:{name}")
                            continue
                        if generation_id not in valid:
                            os.unlink(name, dir_fd=sidecar_fd)
                            report["orphan_sidecars_deleted"].append(name)
                            deleted_any = True
                    except FileNotFoundError:
                        continue
                    except OSError as exc:
                        report["errors"].append(f"{name}:{type(exc).__name__}:{exc}")
                if deleted_any:
                    try:
                        os.fsync(sidecar_fd)
                    except OSError as exc:
                        report["errors"].append(
                            f"sidecar_dir_fsync:{type(exc).__name__}:{exc}"
                        )
                return report
            finally:
                os.close(sidecar_fd)
        finally:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(lock_fd)
    finally:
        os.close(state_fd)
