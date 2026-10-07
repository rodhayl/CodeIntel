from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest

import codeintel.freshness.hardened as hardened_module
from codeintel.core.models import Generation
from codeintel.freshness.barrier import StagingResult
from codeintel.freshness.hardened import HardenedProductionGenerationBarrier
from codeintel.freshness.production import ProductionGenerationBarrier


def _generation(generation_id: str) -> Generation:
    return Generation(
        generation_id=generation_id,
        repo_id="repo",
        sequence=1,
        snapshot_hash="0" * 64,
    )


@pytest.mark.parametrize("exc_type", [KeyboardInterrupt, asyncio.CancelledError])
def test_snapshot_registration_cancellation_aborts_staging(monkeypatch, exc_type):
    barrier = object.__new__(HardenedProductionGenerationBarrier)
    staging = _generation("gen_new")
    events = []

    monkeypatch.setattr(
        ProductionGenerationBarrier,
        "create_staging_generation",
        lambda self: StagingResult(staging, {"a.py": "a" * 64}),
    )
    monkeypatch.setattr(
        HardenedProductionGenerationBarrier,
        "abort_staging_generation",
        lambda self, generation: events.append(("abort", generation.generation_id)),
    )
    monkeypatch.setattr(
        hardened_module,
        "register_generation_snapshot",
        lambda *args, **kwargs: (_ for _ in ()).throw(exc_type("cancel snapshot")),
    )
    monkeypatch.setattr(
        hardened_module,
        "clear_generation_snapshot",
        lambda generation_id, **kwargs: events.append(("clear", generation_id)),
    )

    with pytest.raises(exc_type, match="cancel snapshot"):
        barrier.create_staging_generation()

    assert events == [("abort", "gen_new"), ("clear", "gen_new")]


def _transition_barrier(monkeypatch, exc_type, active_getter):
    barrier = object.__new__(HardenedProductionGenerationBarrier)
    barrier._lock = threading.RLock()
    barrier._active_lease_fd = 101
    barrier._leased_generation_id = "gen_old"
    barrier._active_generation = _generation("gen_old")
    barrier.repo_id = "repo"
    barrier.gen_store = SimpleNamespace(get_active_generation=active_getter)
    barrier._retained_transition_lease_fds = []
    released = []

    monkeypatch.setattr(barrier, "_validate_offline_artifacts", lambda generation_id: None)
    monkeypatch.setattr(barrier, "_open_generation_lease_fd", lambda generation_id: 202)
    monkeypatch.setattr(hardened_module.fcntl, "flock", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        barrier,
        "_release_lease_fd",
        lambda fd: released.append(fd) if fd is not None else None,
    )
    monkeypatch.setattr(
        ProductionGenerationBarrier,
        "commit_staging_generation",
        lambda self, staging, hashes: (_ for _ in ()).throw(exc_type("cancel commit")),
    )
    monkeypatch.setattr(hardened_module, "clear_generation_snapshot", lambda generation_id, **kwargs: None)
    return barrier, released


@pytest.mark.parametrize("exc_type", [KeyboardInterrupt, asyncio.CancelledError])
def test_commit_cancellation_restores_old_lease_when_old_generation_is_authoritative(
    monkeypatch, exc_type
):
    old = _generation("gen_old")
    barrier, released = _transition_barrier(monkeypatch, exc_type, lambda repo_id: old)

    with pytest.raises(exc_type, match="cancel commit"):
        barrier.commit_staging_generation(_generation("gen_new"), {})

    assert barrier._active_lease_fd == 101
    assert barrier._leased_generation_id == "gen_old"
    assert released == [202]
    assert barrier._retained_transition_lease_fds == []


@pytest.mark.parametrize("exc_type", [KeyboardInterrupt, asyncio.CancelledError])
def test_commit_cancellation_retains_both_candidates_when_authority_is_unavailable(
    monkeypatch, exc_type
):
    def unavailable(repo_id):
        raise RuntimeError("authority unavailable")

    barrier, released = _transition_barrier(monkeypatch, exc_type, unavailable)

    with pytest.raises(exc_type, match="cancel commit"):
        barrier.commit_staging_generation(_generation("gen_new"), {})

    assert barrier._active_lease_fd == 202
    assert barrier._leased_generation_id == "gen_new"
    assert barrier._retained_transition_lease_fds == [101]
    assert released == []
    barrier._retained_transition_lease_fds = []
