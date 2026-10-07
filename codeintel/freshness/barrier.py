from codeintel.compat import fcntl
import hashlib
import importlib.metadata as importlib_metadata
import json
import os
import threading
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from codeintel.core.contracts import (
    INDEX_CHUNK_POLICY_VERSION, INDEX_SNAPSHOT_FORMAT_VERSION,
    MAX_INDEX_CHUNK_BYTES,
)
from codeintel.core.identity import canonical_hash
from codeintel.core.models import Generation
from codeintel.core.security import (
    FreshnessBusyError,
    RepoJail,
    get_trusted_state_dir,
    validate_trusted_state_dir,
)
from codeintel.storage.base import GenerationStore


@dataclass
class StagingResult:
    generation: Generation
    current_hashes: Dict[str, str]
    reused_existing: bool = False
    git_head: Optional[str] = None
    ignore_fingerprint: Optional[str] = None


def _installed_version(distribution: str, declared_fallback: str) -> str:
    try:
        return importlib_metadata.version(distribution)
    except importlib_metadata.PackageNotFoundError:
        return f"missing:{declared_fallback}"
    except Exception:
        return f"unknown:{declared_fallback}"


def compute_index_build_fingerprint() -> str:
    """Identify only the maintained parser, grammar, chunk, schema and snapshot policy."""
    spec = {
        "schema": "2.0.0-generation-local-fts",
        "parser": "treesitter-2026.10-syntax-owner-v3",
        "grammars": {
            name: {"declared": declared, "runtime": _installed_version(name, declared)}
            for name, declared in (
                ("tree-sitter", "0.26.0"),
                ("tree-sitter-python", "0.25.0"),
                ("tree-sitter-javascript", "0.25.0"),
                ("tree-sitter-typescript", "0.23.2"),
            )
        },
        "chunk_policy": INDEX_CHUNK_POLICY_VERSION,
        "chunk_bytes": MAX_INDEX_CHUNK_BYTES,
        "snapshot_format": INDEX_SNAPSHOT_FORMAT_VERSION,
    }
    framed = json.dumps(spec, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(framed.encode("utf-8")).hexdigest()[:24]


class GenerationBarrier:
    """Shared lifecycle state; concrete safety boundaries live in the production subclasses."""

    def __init__(self, repo_id: str, repo_root: str, gen_store: GenerationStore,
                 state_dir: Optional[str] = None, build_fingerprint: Optional[str] = None):
        self.repo_id = repo_id
        self.repo_root = os.path.abspath(repo_root)
        self.gen_store = gen_store
        self.state_dir = (
            validate_trusted_state_dir(self.repo_root, state_dir)
            if state_dir is not None else get_trusted_state_dir(self.repo_root)
        )
        self.build_fingerprint = build_fingerprint or compute_index_build_fingerprint()
        self.jail = RepoJail(self.repo_root)
        self._lock = threading.RLock()
        self._rebuild_lock_file: Optional[int] = None
        self._active_lease_fd: Optional[int] = None
        self._leased_generation_id: Optional[str] = None
        self.last_gc_report: Dict[str, object] = {"deleted": [], "leased": [], "errors": []}
        self.is_rebuilding = False
        self._active_generation = self.gen_store.get_active_generation(self.repo_id)
        if self._active_generation:
            self._switch_generation_lease(self._active_generation.generation_id)
        existing_gens = self.gen_store.list_generations(self.repo_id)
        self.sequence = max([g.sequence for g in existing_gens] or [0])
        self.file_hashes: Optional[Dict[str, str]] = None
        self.active_git_head: Optional[str] = None
        self.ignore_fingerprint: str = self._compute_ignore_fingerprint()

        if self._active_generation:
            meta = getattr(self._active_generation, "metadata", {}) or {}
            persisted_fp = meta.get("build_fingerprint")
            if persisted_fp != self.build_fingerprint:
                return
            hashes, head, ignore = self._stable_snapshot()
            if self._snapshot_hash(hashes) == self._active_generation.snapshot_hash:
                self.file_hashes = hashes
                self.active_git_head = head
                self.ignore_fingerprint = ignore

    @staticmethod
    def _snapshot_hash(hashes: Dict[str, str]) -> str:
        framed = json.dumps(
            {"schema": INDEX_SNAPSHOT_FORMAT_VERSION, "files": sorted(hashes.items())},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
        )
        return canonical_hash(framed)

    @property
    def active_generation(self) -> Optional[Generation]:
        with self._lock:
            active = self.gen_store.get_active_generation(self.repo_id)
            if active:
                self._active_generation = active
                self._switch_generation_lease(active.generation_id)
            return self._active_generation

    def _stable_snapshot(self, attempts: int = 3) -> Tuple[Dict[str, str], Optional[str], str]:
        for _ in range(max(1, attempts)):
            head_before = self._get_git_head()
            ignore_before = self._compute_ignore_fingerprint()
            hashes = self.scan_worktree()
            head_after = self._get_git_head()
            ignore_after = self._compute_ignore_fingerprint()
            if head_before == head_after and ignore_before == ignore_after:
                return hashes, head_after, ignore_after
        raise FreshnessBusyError("Repository changed while capturing index snapshot")

    def release_rebuild_lock(self) -> None:
        fd = self._rebuild_lock_file
        if fd is None:
            return
        self._rebuild_lock_file = None
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except BaseException as error:
            try:
                os.close(fd)
            except BaseException as cleanup_error:
                error.add_note(f"Rebuild descriptor cleanup also failed: {cleanup_error}")
            raise
        else:
            os.close(fd)

    def _persist_identity(self, generation_id: str, head: Optional[str], ignore: str) -> None:
        updater = getattr(self.gen_store, "update_generation_metadata", None)
        if callable(updater):
            updater(generation_id, {
                "build_fingerprint": getattr(self, "build_fingerprint", None),
                "git_head": head,
                "ignore_fingerprint": ignore,
            })

    def create_staging_generation(self) -> StagingResult:
        with self._lock:
            self.acquire_rebuild_lock()
            self.is_rebuilding = True
            try:
                current_hashes, git_head, ignore_fp = self._stable_snapshot()
                snapshot_hash = self._snapshot_hash(current_hashes)
                active = self.active_generation
                if (
                    active is not None
                    and active.is_active
                    and active.snapshot_hash == snapshot_hash
                    and getattr(active, "build_fingerprint", None) == self.build_fingerprint
                    and self.file_hashes == current_hashes
                    and self.active_git_head == git_head
                    and self.ignore_fingerprint == ignore_fp
                ):
                    self.is_rebuilding = False
                    self.release_rebuild_lock()
                    return StagingResult(
                        generation=active,
                        current_hashes=current_hashes,
                        reused_existing=True,
                        git_head=git_head,
                        ignore_fingerprint=ignore_fp,
                    )
                existing = self.gen_store.list_generations(self.repo_id)
                self.sequence = max([g.sequence for g in existing] or [0]) + 1
                staging_gen = self.gen_store.create_generation(
                    repo_id=self.repo_id, sequence=self.sequence, snapshot_hash=snapshot_hash,
                    build_fingerprint=self.build_fingerprint,
                    metadata={"build_fingerprint": self.build_fingerprint, "git_head": git_head,
                              "ignore_fingerprint": ignore_fp, "indexing_mode": "FULL_REBUILD_V1",
                              "lifecycle_state": "STAGING"},
                )
                return StagingResult(staging_gen, current_hashes, False, git_head, ignore_fp)
            except BaseException:
                self.is_rebuilding = False
                self.release_rebuild_lock()
                raise

    def commit_staging_generation(self, staging_gen: Generation, new_hashes: Dict[str, str]) -> None:
        with self._lock:
            try:
                verify_hashes, git_head, ignore_fp = self._stable_snapshot()
                if verify_hashes != new_hashes:
                    raise FreshnessBusyError("Repository changed while staging index; activation refused")
                if self._snapshot_hash(verify_hashes) != staging_gen.snapshot_hash:
                    raise FreshnessBusyError("Staging generation snapshot hash does not match verified worktree")
                self._persist_identity(staging_gen.generation_id, git_head, ignore_fp)
                updater = getattr(self.gen_store, "update_generation_metadata", None)
                if callable(updater):
                    updater(staging_gen.generation_id, {"lifecycle_state": "COMMITTED"})
                self.gen_store.activate_generation(staging_gen.generation_id)
                refreshed = self.gen_store.get_active_generation(self.repo_id)
                self._active_generation = refreshed or staging_gen
                self._switch_generation_lease(staging_gen.generation_id)
                self.file_hashes = dict(verify_hashes)
                self.active_git_head = git_head
                self.ignore_fingerprint = ignore_fp
                # GC is deliberately non-fatal after activation; leases protect cross-process readers.
                self._gc_generations()
                self.is_rebuilding = False
                self.release_rebuild_lock()
            except BaseException:
                self.is_rebuilding = False
                self.release_rebuild_lock()
                raise

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
