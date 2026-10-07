import hashlib
import os
import stat
from pathlib import Path
from typing import Mapping, Optional, Set

STATE_LAYOUT_VERSION = "2"
STATE_LAYOUT_MARKER = ".codeintel_state_layout"
MAX_STATE_LAYOUT_MARKER_BYTES = 64

DEFAULT_EXCLUDED_DIRS = {
    ".git", ".venv", "venv", "node_modules", "dist", "build", "__pycache__",
    ".pytest_cache", ".mypy_cache", ".tox", ".eggs", "target", "vendor",
    ".idea", ".vscode", "coverage", ".turbo", ".next", ".repowise"
}

BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".tar",
    ".gz", ".7z", ".rar", ".exe", ".dll", ".so", ".dylib", ".bin", ".pyc",
    ".pkl", ".parquet", ".arrow", ".onnx", ".pt", ".safetensors",
    ".db", ".db-wal", ".db-shm", ".lbug", ".sqlite", ".sqlite3",
    ".jsonl"  # Record streams and training corpora are data, not code source.
}


class SecurityException(Exception):
    pass


class FreshnessBusyError(Exception):
    """Raised when repository mutations prevent a stable generation."""
    pass


def _is_within(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def _read_state_layout_marker(marker: Path) -> str:
    # Open nonblocking so a FIFO cannot hang before fstat rejects its type.
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        fd = os.open(marker, flags)
    except OSError as exc:
        raise SecurityException(f"Cannot open CodeIntel state-layout marker safely: {marker}") from exc
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise SecurityException(f"CodeIntel state-layout marker must be a regular file: {marker}")
        if before.st_size > MAX_STATE_LAYOUT_MARKER_BYTES:
            raise SecurityException(f"CodeIntel state-layout marker is unexpectedly large: {marker}")
        raw = os.read(fd, MAX_STATE_LAYOUT_MARKER_BYTES + 1)
        if len(raw) > MAX_STATE_LAYOUT_MARKER_BYTES:
            raise SecurityException(f"CodeIntel state-layout marker exceeded size limit: {marker}")
        after = os.fstat(fd)
        if (
            before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or len(raw) != after.st_size
        ):
            raise SecurityException(f"CodeIntel state-layout marker changed while being read: {marker}")
        try:
            return raw.decode("utf-8", errors="strict").strip()
        except UnicodeDecodeError as exc:
            raise SecurityException(f"CodeIntel state-layout marker is not UTF-8: {marker}") from exc
    finally:
        os.close(fd)


def is_symlink_or_reparse(path: Path | str) -> bool:
    """Portable check for symbolic links, directory junctions, and reparse points.

    On Windows, directory junctions (mklink /J) and mount points return False for
    Path.is_symlink() and os.path.islink(). This function checks Path.is_symlink(),
    Path.is_junction(), and the FILE_ATTRIBUTE_REPARSE_POINT attribute / st_reparse_tag.
    """
    p = Path(path)
    if p.is_symlink():
        return True
    if hasattr(p, "is_junction") and p.is_junction():
        return True
    try:
        st = os.lstat(p)
        reparse_attr = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        if getattr(st, "st_file_attributes", 0) & reparse_attr:
            return True
        if getattr(st, "st_reparse_tag", 0) != 0:
            return True
    except OSError:
        pass
    return False


def validate_trusted_state_dir(repo_root: str, state_dir: str) -> str:
    """Validate and create an engine-owned state directory outside the worktree."""
    repo = Path(repo_root).expanduser().resolve()
    requested_input = Path(state_dir).expanduser()
    if is_symlink_or_reparse(requested_input):
        raise SecurityException(f"Trusted state directory may not be a symlink: {requested_input}")
    if not requested_input.is_absolute():
        requested = (Path.cwd() / requested_input).resolve()
    else:
        requested = requested_input.resolve()
    if requested == repo or _is_within(requested, repo):
        raise SecurityException(
            f"CodeIntel state directory must be outside the untrusted repository: {requested}"
        )
    for ancestor in (requested_input, *requested_input.parents):
        if is_symlink_or_reparse(ancestor):
            raise SecurityException(
                f"Trusted state directory or parent is a symlink or reparse point: {ancestor}"
            )
    requested.mkdir(mode=0o700, parents=True, exist_ok=True)
    if is_symlink_or_reparse(requested) or not requested.is_dir():
        raise SecurityException(f"Trusted state path is not a real directory: {requested}")
    try:
        st = requested.stat()
        if hasattr(os, "geteuid") and st.st_uid != os.geteuid():
            raise SecurityException(
                f"Trusted state directory is owned by uid {st.st_uid}, expected {os.geteuid()}"
            )
        os.chmod(requested, 0o700)
    except SecurityException:
        raise
    except OSError as exc:
        raise SecurityException(
            f"Could not enforce private permissions on trusted state directory: {requested}"
        ) from exc
    marker = requested / STATE_LAYOUT_MARKER
    if os.path.lexists(marker):
        version = _read_state_layout_marker(marker)
        if version != STATE_LAYOUT_VERSION:
            raise SecurityException(
                f"Incompatible CodeIntel derived-state layout {version!r}; expected {STATE_LAYOUT_VERSION!r}. "
                "Rebuild the derived state in a fresh directory."
            )
    else:
        existing = [entry for entry in requested.iterdir() if entry.name != STATE_LAYOUT_MARKER]
        if existing:
            raise SecurityException(
                f"Unversioned CodeIntel state already exists at {requested}; rebuild it in a fresh directory."
            )
        try:
            fd = os.open(
                marker,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_BINARY", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
        except FileExistsError:
            version = _read_state_layout_marker(marker)
            if version != STATE_LAYOUT_VERSION:
                raise SecurityException(f"Concurrent state-layout mismatch at {marker}")
        else:
            try:
                payload = (STATE_LAYOUT_VERSION + "\n").encode("utf-8")
                offset = 0
                while offset < len(payload):
                    written = os.write(fd, payload[offset:])
                    if written <= 0:
                        raise OSError("state-layout marker write made no progress")
                    offset += written
                os.fsync(fd)
            except BaseException:
                # This process created the exclusive marker. Do not leave a partial
                # file that would permanently poison later state validation.
                try:
                    os.close(fd)
                except OSError:
                    pass
                try:
                    os.unlink(marker)
                except FileNotFoundError:
                    pass
                raise
            finally:
                try:
                    os.close(fd)
                except OSError:
                    pass
    return str(requested)


def get_trusted_state_dir(
    repo_root: str, *, environ: Optional[Mapping[str, str]] = None
) -> str:
    """Return deterministic private per-repository state for the supplied process environment."""
    env = os.environ if environ is None else environ
    home_value = env.get("HOME") if environ is not None else None
    if home_value and not Path(home_value).is_absolute():
        raise SecurityException("HOME in the target environment must be absolute")
    home = Path(home_value) if home_value else Path.home()

    def expand_state_path(raw: str) -> Path:
        if environ is not None and raw in {"~", "~/"}:
            return home
        if environ is not None and raw.startswith("~/"):
            return home / raw[2:]
        return Path(raw).expanduser()

    # Keep lexical ancestors until the shared validator can reject symlinks.
    # Resolving here would silently erase the path the caller actually supplied.
    custom_state = env.get("CODEINTEL_STATE_DIR")
    if custom_state:
        state_root = expand_state_path(custom_state)
        if not state_root.is_absolute():
            state_root = Path.cwd() / state_root
    else:
        xdg_data = env.get("XDG_DATA_HOME")
        if xdg_data:
            state_root = expand_state_path(xdg_data)
            if not state_root.is_absolute():
                state_root = home / ".local" / "share"
        else:
            state_root = home / ".local" / "share" / "codeintel"

    canonical_root = str(Path(repo_root).expanduser().resolve())
    repo_hash = hashlib.sha256(canonical_root.encode("utf-8")).hexdigest()[:16]
    repo_name = Path(canonical_root).name or "root"
    if custom_state:
        state_dir = state_root / "repos" / f"{repo_name}_{repo_hash}"
    elif env.get("XDG_DATA_HOME"):
        state_dir = state_root / "codeintel" / "repos" / f"{repo_name}_{repo_hash}"
    else:
        state_dir = state_root / "repos" / f"{repo_name}_{repo_hash}"
    return validate_trusted_state_dir(canonical_root, str(state_dir))


class RepoJail:
    """Strict repository containment for all file reads/index operations."""

    def __init__(self, repo_root: str):
        self.repo_root = Path(repo_root).expanduser().resolve()
        if not self.repo_root.exists() or not self.repo_root.is_dir():
            raise SecurityException(f"Repository root does not exist or is not a directory: {repo_root}")

    def safe_relpath(self, target_path: str) -> str:
        if not isinstance(target_path, str) or not target_path or "\x00" in target_path:
            raise SecurityException("Invalid repository path")
        # This is a physical repository candidate, not user configuration.
        # Tilde-prefixed names from Git/scandir must remain literal filenames.
        target = Path(target_path)
        full_target = target if target.is_absolute() else self.repo_root / target
        try:
            resolved = full_target.resolve()
            rel = resolved.relative_to(self.repo_root)
        except (ValueError, RuntimeError, OSError):
            raise SecurityException(f"Path traversal / symlink escape attempt detected: {target_path}")
        if str(rel) in ("", "."):
            raise SecurityException("Repository root is not a file path")
        canonical = rel.as_posix()
        # A literal POSIX backslash is a filename byte, not a path separator.
        # Reinterpreting it after containment validation can manufacture ../ or /.
        if "\\" in canonical:
            raise SecurityException("Literal backslash names are not canonical repository paths")
        return canonical

    def is_path_allowed(self, rel_path: str, custom_excludes: Optional[Set[str]] = None) -> bool:
        try:
            normalized = self.safe_relpath(rel_path)
        except SecurityException:
            return False
        parts = Path(normalized).parts
        excludes = DEFAULT_EXCLUDED_DIRS | (custom_excludes or set())
        for part in parts:
            if part in excludes or part.startswith(".codeintel") or part.endswith(".db"):
                return False
        curr = self.repo_root
        for part in parts[:-1]:
            curr = curr / part
            if (curr / ".git").exists():
                return False
        return Path(normalized).suffix.lower() not in BINARY_EXTENSIONS


def is_binary_content(raw_bytes: bytes) -> bool:
    """Conservatively detect binary payloads while permitting valid UTF-8 source."""
    if not raw_bytes:
        return False
    sample = raw_bytes[:8192]
    if b"\x00" in sample:
        return True
    controls = sum(
        1 for b in sample if (b < 32 and b not in (9, 10, 12, 13)) or b == 127
    )
    if (controls / len(sample)) > 0.10:
        return True
    try:
        raw_bytes.decode("utf-8", errors="strict")
        return False
    except UnicodeDecodeError:
        return True
