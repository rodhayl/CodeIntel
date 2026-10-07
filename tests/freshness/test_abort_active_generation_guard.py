import threading
from types import SimpleNamespace

from codeintel.freshness.hardened import HardenedProductionGenerationBarrier


class _Store:
    def __init__(self, active):
        self.active = active
        self.deleted = []

    def get_active_generation(self, repo_id):
        assert repo_id == "repo_test"
        return self.active

    def delete_generation_data(self, generation_id):
        self.deleted.append(generation_id)


def _barrier(store):
    barrier = object.__new__(HardenedProductionGenerationBarrier)
    barrier._lock = threading.RLock()
    barrier.gen_store = store
    barrier.repo_id = "repo_test"
    barrier._active_generation = SimpleNamespace(generation_id="gen_old")
    barrier._leased_generation_id = "gen_old"
    barrier._active_lease_fd = None
    barrier._rebuild_lock_file = None
    barrier.is_rebuilding = True
    barrier.release_rebuild_lock = lambda: None
    return barrier


def test_abort_never_deletes_generation_already_active_in_authoritative_store():
    active = SimpleNamespace(generation_id="gen_new", is_active=True)
    staging = SimpleNamespace(generation_id="gen_new", is_active=False)
    store = _Store(active)
    barrier = _barrier(store)
    removed = []
    barrier._remove_generation_sidecar = removed.append
    switched = []
    barrier._switch_generation_lease = switched.append

    barrier.abort_staging_generation(staging)

    assert store.deleted == []
    assert removed == []
    assert barrier._active_generation is active
    assert switched == ["gen_new"]
    assert barrier.is_rebuilding is False


def test_abort_preserves_active_generation_even_if_lease_reconciliation_fails():
    active = SimpleNamespace(generation_id="gen_new", is_active=True)
    staging = SimpleNamespace(generation_id="gen_new", is_active=False)
    store = _Store(active)
    barrier = _barrier(store)
    removed = []
    barrier._remove_generation_sidecar = removed.append

    def fail_switch(_generation_id):
        raise RuntimeError("lease unavailable")

    barrier._switch_generation_lease = fail_switch
    barrier.abort_staging_generation(staging)

    assert store.deleted == []
    assert removed == []
    assert barrier._active_generation is active


def test_abort_deletes_only_nonactive_staging_generation():
    active = SimpleNamespace(generation_id="gen_old", is_active=True)
    staging = SimpleNamespace(generation_id="gen_new", is_active=False)
    store = _Store(active)
    barrier = _barrier(store)
    removed = []
    barrier._remove_generation_sidecar = removed.append
    barrier._switch_generation_lease = lambda _generation_id: None

    barrier.abort_staging_generation(staging)

    assert store.deleted == ["gen_new"]
    assert removed == ["gen_new"]
