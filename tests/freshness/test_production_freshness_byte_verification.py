import threading
from types import SimpleNamespace

from codeintel.freshness.hardened import HardenedProductionGenerationBarrier as ProductionGenerationBarrier


class _Store:
    def __init__(self, active):
        self.active = active

    def get_active_generation(self, repo_id):
        return self.active


def test_production_freshness_does_not_trust_clean_git_metadata_fast_path(monkeypatch):
    prior = {"pkg/source.py": "old-hash"}
    current = {"pkg/source.py": "new-hash"}
    active = SimpleNamespace(
        snapshot_hash=ProductionGenerationBarrier._snapshot_hash(prior),
        metadata={"build_fingerprint": "fp"},
    )
    barrier = object.__new__(ProductionGenerationBarrier)
    barrier._lock = threading.RLock()
    barrier.repo_id = "repo"
    barrier.gen_store = _Store(active)
    barrier._active_generation = active
    barrier.file_hashes = dict(prior)
    barrier.build_fingerprint = "fp"
    barrier.active_git_head = "same-head"
    barrier.ignore_fingerprint = "same-ignore"

    monkeypatch.setattr(
        barrier,
        "_stable_snapshot",
        lambda: (dict(current), "same-head", "same-ignore"),
    )
    # These are exactly the metadata conditions that made the old implementation
    # return fresh without reading repository bytes.
    monkeypatch.setattr(barrier, "_get_git_head", lambda: "same-head")
    monkeypatch.setattr(barrier, "_compute_ignore_fingerprint", lambda: "same-ignore")

    fresh, added, modified, deleted = barrier.check_freshness()

    assert fresh is False
    assert added == []
    assert modified == ["pkg/source.py"]
    assert deleted == []
