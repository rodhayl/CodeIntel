import os
import threading
from types import SimpleNamespace

import pytest

from codeintel.freshness.hardened import HardenedProductionGenerationBarrier


class _UncertainStore:
    def __init__(self):
        self.active = SimpleNamespace(generation_id="gen_old", is_active=True)
        self.activation_crossed = False

    def update_generation_metadata(self, generation_id, metadata):
        pass

    def activate_generation(self, generation_id):
        self.active = SimpleNamespace(generation_id=generation_id, is_active=True)
        self.activation_crossed = True

    def get_active_generation(self, repo_id):
        assert repo_id == "repo_test"
        if self.activation_crossed:
            raise RuntimeError("authority temporarily unavailable")
        return self.active


def _barrier(tmp_path, store):
    old_fd = os.open(tmp_path / "old.lease", os.O_CREAT | os.O_RDWR, 0o600)
    barrier = object.__new__(HardenedProductionGenerationBarrier)
    barrier._lock = threading.RLock()
    barrier.gen_store = store
    barrier.repo_id = "repo_test"
    barrier._active_generation = store.active
    barrier._active_lease_fd = old_fd
    barrier._leased_generation_id = "gen_old"
    barrier._retained_transition_lease_fds = []
    barrier.file_hashes = {"a.py": "hash"}
    barrier.active_git_head = "old-head"
    barrier.ignore_fingerprint = "old-ignore"
    barrier.build_fingerprint = "build"
    barrier.is_rebuilding = True
    barrier._rebuild_lock_file = None
    barrier._validate_offline_artifacts = lambda _generation_id: None
    barrier._stable_snapshot = lambda: ({"a.py": "hash"}, "head", "ignore")
    barrier._snapshot_hash = lambda _hashes: "snapshot"
    barrier._gc_generations = lambda: {}
    barrier.release_rebuild_lock = lambda: None
    barrier._open_generation_lease_fd = lambda _generation_id: os.open(
        tmp_path / "new.lease", os.O_CREAT | os.O_RDWR, 0o600
    )
    staging = SimpleNamespace(generation_id="gen_new", snapshot_hash="snapshot")
    return barrier, staging, old_fd


def test_unknown_sqlite_activation_outcome_retains_both_generation_leases(tmp_path):
    store = _UncertainStore()
    barrier, staging, old_fd = _barrier(tmp_path, store)

    with pytest.raises(RuntimeError, match="authority temporarily unavailable"):
        barrier.commit_staging_generation(staging, {"a.py": "hash"})

    # SQLite did in fact activate the new generation, but the barrier was unable to
    # prove that after the exception. Fail closed by protecting BOTH possibilities.
    assert store.active.generation_id == "gen_new"
    assert barrier._leased_generation_id == "gen_new"
    new_fd = barrier._active_lease_fd
    os.fstat(new_fd)
    assert barrier._retained_transition_lease_fds == [old_fd]
    os.fstat(old_fd)

    barrier._release_lease_fd(new_fd)
    barrier._active_lease_fd = None
    barrier._release_retained_transition_leases()
    with pytest.raises(OSError):
        os.fstat(old_fd)


def test_close_releases_retained_transition_leases_even_when_super_close_fails(monkeypatch, tmp_path):
    store = _UncertainStore()
    barrier, _staging, old_fd = _barrier(tmp_path, store)
    barrier._retained_transition_lease_fds = [old_fd]

    def fail_close(self):
        raise RuntimeError("close failed")

    monkeypatch.setattr(
        "codeintel.freshness.production.ProductionGenerationBarrier.close",
        fail_close,
    )

    with pytest.raises(RuntimeError, match="close failed"):
        barrier.close()
    assert barrier._retained_transition_lease_fds == []
    with pytest.raises(OSError):
        os.fstat(old_fd)
    barrier._release_lease_fd(barrier._active_lease_fd)
    barrier._active_lease_fd = None
