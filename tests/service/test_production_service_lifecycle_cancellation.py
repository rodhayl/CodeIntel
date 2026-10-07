from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import codeintel.service.production as production_module
from codeintel.service.production import ProductionDomainService


class _CloseTracker:
    def __init__(self, failure=None):
        self.closed = 0
        self.failure = failure

    def close(self):
        self.closed += 1
        if self.failure is not None:
            raise self.failure


def test_close_continues_to_owned_storage_after_barrier_cancellation():
    service = object.__new__(ProductionDomainService)
    service._closed = False
    service._owns_storage = True
    service.barrier = _CloseTracker(KeyboardInterrupt("barrier cancelled"))
    store = _CloseTracker()
    service.graph_store = store
    service.lexical_index = store
    service.generation_store = store

    with pytest.raises(KeyboardInterrupt, match="barrier cancelled"):
        service.close()

    assert service.barrier.closed == 1
    assert store.closed == 1
    assert service._closed is False


def test_constructor_closes_barrier_when_post_barrier_initialization_is_cancelled(
    tmp_path, monkeypatch
):
    barrier = _CloseTracker()
    barrier.active_generation = None

    monkeypatch.setattr(
        production_module,
        "SemanticMerger",
        lambda: SimpleNamespace(),
    )
    monkeypatch.setattr(production_module, "compute_index_build_fingerprint", lambda **kwargs: "fp")
    monkeypatch.setattr(
        production_module,
        "HardenedProductionGenerationBarrier",
        lambda *args, **kwargs: barrier,
    )

    class CancelledRetriever:
        def __init__(self, *args, **kwargs):
            raise asyncio.CancelledError("cancel retriever init")

    monkeypatch.setattr(production_module, "HybridRetriever", CancelledRetriever)
    monkeypatch.setattr(
        production_module,
        "validate_trusted_state_dir",
        lambda repo_root, state_dir: str(tmp_path),
    )

    with pytest.raises(asyncio.CancelledError, match="cancel retriever init"):
        ProductionDomainService(
            repo_root=str(tmp_path),
            state_dir=str(tmp_path),
            graph_store=SimpleNamespace(),
            lexical_index=SimpleNamespace(),
            generation_store=SimpleNamespace(),
        )

    assert barrier.closed == 1


def test_factory_closes_store_when_service_construction_is_cancelled(tmp_path, monkeypatch):
    store = _CloseTracker()
    monkeypatch.setattr(production_module, "ProductionSQLiteStore", lambda path: store)
    monkeypatch.setattr(
        production_module,
        "validate_trusted_state_dir",
        lambda repo_root, state_dir: str(tmp_path),
    )

    def cancelled_service(*args, **kwargs):
        raise asyncio.CancelledError("cancel service construction")

    monkeypatch.setattr(production_module, "ProductionDomainService", cancelled_service)

    with pytest.raises(asyncio.CancelledError, match="cancel service construction"):
        production_module.create_default_service(
            str(tmp_path),
            state_dir=str(tmp_path),
            enable_dense=False,
        )

    assert store.closed == 1
