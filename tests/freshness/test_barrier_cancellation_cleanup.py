from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest

from codeintel.core.models import Generation
from codeintel.freshness.barrier import GenerationBarrier


def _bare_barrier():
    barrier = object.__new__(GenerationBarrier)
    barrier._lock = threading.RLock()
    barrier.repo_id = "repo"
    barrier.sequence = 0
    barrier.file_hashes = None
    barrier.active_git_head = None
    barrier.ignore_fingerprint = "ignore"
    barrier.build_fingerprint = "build_fp"
    barrier.is_rebuilding = False
    barrier._rebuild_lock_file = None
    barrier._active_generation = None
    barrier._active_lease_fd = None
    barrier._leased_generation_id = None
    return barrier


@pytest.mark.parametrize("exc_type", [KeyboardInterrupt, asyncio.CancelledError])
def test_create_staging_releases_rebuild_state_on_cancellation(monkeypatch, exc_type):
    barrier = _bare_barrier()
    events = []
    monkeypatch.setattr(barrier, "acquire_rebuild_lock", lambda: events.append("acquire"), raising=False)
    monkeypatch.setattr(barrier, "release_rebuild_lock", lambda: events.append("release"))
    monkeypatch.setattr(
        barrier,
        "_stable_snapshot",
        lambda: (_ for _ in ()).throw(exc_type("cancel snapshot")),
    )

    with pytest.raises(exc_type, match="cancel snapshot"):
        barrier.create_staging_generation()

    assert events == ["acquire", "release"]
    assert barrier.is_rebuilding is False


@pytest.mark.parametrize("exc_type", [KeyboardInterrupt, asyncio.CancelledError])
def test_commit_releases_rebuild_state_on_cancellation(monkeypatch, exc_type):
    barrier = _bare_barrier()
    barrier.is_rebuilding = True
    events = []
    hashes = {"a.py": "a" * 64}
    generation = Generation(
        generation_id="gen_1",
        repo_id="repo",
        sequence=1,
        snapshot_hash="snapshot",
    )
    monkeypatch.setattr(barrier, "_stable_snapshot", lambda: (hashes, None, "ignore"))
    monkeypatch.setattr(barrier, "_snapshot_hash", lambda value: "snapshot")
    monkeypatch.setattr(barrier, "release_rebuild_lock", lambda: events.append("release"))
    barrier.gen_store = SimpleNamespace(
        update_generation_metadata=lambda *args, **kwargs: (_ for _ in ()).throw(
            exc_type("cancel commit")
        )
    )

    with pytest.raises(exc_type, match="cancel commit"):
        barrier.commit_staging_generation(generation, hashes)

    assert events == ["release"]
    assert barrier.is_rebuilding is False
