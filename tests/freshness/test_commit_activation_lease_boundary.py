import os
import threading
from types import SimpleNamespace

import pytest

from codeintel.freshness.hardened import HardenedProductionGenerationBarrier


class _Store:
    def __init__(self):
        self.active = SimpleNamespace(generation_id="gen_old", is_active=True)
        self.activate_calls = []
        self.metadata_updates = []

    def update_generation_metadata(self, generation_id, metadata):
        self.metadata_updates.append((generation_id, dict(metadata)))

    def activate_generation(self, generation_id):
        self.activate_calls.append(generation_id)
        self.active = SimpleNamespace(generation_id=generation_id, is_active=True)

    def get_active_generation(self, repo_id):
        assert repo_id == "repo_test"
        return self.active


def _make_barrier(tmp_path, store):
    old_path = tmp_path / "old.lease"
    old_fd = os.open(old_path, os.O_CREAT | os.O_RDWR, 0o600)
    barrier = object.__new__(HardenedProductionGenerationBarrier)
    barrier._lock = threading.RLock()
    barrier.gen_store = store
    barrier.repo_id = "repo_test"
    barrier._active_generation = store.active
    barrier._active_lease_fd = old_fd
    barrier._leased_generation_id = "gen_old"
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

    new_path = tmp_path / "new.lease"
    barrier._open_generation_lease_fd = lambda _generation_id: os.open(
        new_path, os.O_CREAT | os.O_RDWR, 0o600
    )
    staging = SimpleNamespace(generation_id="gen_new", snapshot_hash="snapshot")
    return barrier, staging, old_fd


def _assert_closed(fd):
    with pytest.raises(OSError):
        os.fstat(fd)


def test_commit_acquires_new_lease_before_sqlite_activation_and_retires_old_after_success(tmp_path):
    store = _Store()
    barrier, staging, old_fd = _make_barrier(tmp_path, store)
    observed = []
    original_activate = store.activate_generation

    def activate(generation_id):
        try:
            os.fstat(old_fd)
            old_open_during_activation = True
        except OSError:
            old_open_during_activation = False
        observed.append(
            (
                barrier._leased_generation_id,
                barrier._active_lease_fd,
                old_open_during_activation,
            )
        )
        original_activate(generation_id)

    store.activate_generation = activate
    barrier.commit_staging_generation(staging, {"a.py": "hash"})

    assert store.activate_calls == ["gen_new"]
    assert observed and observed[0][0] == "gen_new"
    assert observed[0][1] != old_fd
    assert observed[0][2] is True
    assert barrier._leased_generation_id == "gen_new"
    os.fstat(barrier._active_lease_fd)
    _assert_closed(old_fd)
    barrier._release_lease_fd(barrier._active_lease_fd)
    barrier._active_lease_fd = None


def test_commit_does_not_touch_sqlite_when_prospective_lease_cannot_be_acquired(tmp_path):
    store = _Store()
    barrier, staging, old_fd = _make_barrier(tmp_path, store)

    def fail_open(_generation_id):
        raise RuntimeError("lease unavailable")

    barrier._open_generation_lease_fd = fail_open
    with pytest.raises(RuntimeError, match="lease unavailable"):
        barrier.commit_staging_generation(staging, {"a.py": "hash"})

    assert store.activate_calls == []
    assert barrier._leased_generation_id == "gen_old"
    assert barrier._active_lease_fd == old_fd
    os.fstat(old_fd)
    barrier._release_lease_fd(old_fd)
    barrier._active_lease_fd = None


def test_post_activation_failure_keeps_new_lease_and_active_generation(tmp_path):
    store = _Store()
    barrier, staging, old_fd = _make_barrier(tmp_path, store)

    def fail_release():
        raise RuntimeError("release failed")

    barrier.release_rebuild_lock = fail_release
    with pytest.raises(RuntimeError, match="release failed"):
        barrier.commit_staging_generation(staging, {"a.py": "hash"})

    assert store.active.generation_id == "gen_new"
    assert store.activate_calls == ["gen_new"]
    assert barrier._active_generation.generation_id == "gen_new"
    assert barrier._leased_generation_id == "gen_new"
    os.fstat(barrier._active_lease_fd)
    _assert_closed(old_fd)
    barrier._release_lease_fd(barrier._active_lease_fd)
    barrier._active_lease_fd = None
