from types import SimpleNamespace

import pytest

import codeintel.service.production as production
from codeintel.service.production import ProductionDomainService


def test_direct_production_service_validates_explicit_state_dir(monkeypatch, tmp_path):
    calls = []

    def reject(repo_root, state_dir):
        calls.append((repo_root, state_dir))
        raise RuntimeError("state rejected")

    monkeypatch.setattr(production, "validate_trusted_state_dir", reject)

    with pytest.raises(RuntimeError, match="state rejected"):
        ProductionDomainService(
            repo_root=str(tmp_path),
            state_dir=str(tmp_path / "unsafe-state"),
            graph_store=object(),
            lexical_index=object(),
            generation_store=object(),
        )

    assert calls == [(str(tmp_path), str(tmp_path / "unsafe-state"))]


def test_failed_close_remains_retryable():
    class FlakyBarrier:
        def __init__(self):
            self.calls = 0

        def close(self):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("first close failed")

    service = object.__new__(ProductionDomainService)
    service._closed = False
    service._owns_storage = False
    service.barrier = FlakyBarrier()
    service.graph_store = SimpleNamespace()
    service.lexical_index = service.graph_store
    service.generation_store = service.graph_store

    with pytest.raises(RuntimeError, match="first close failed"):
        service.close()
    assert service._closed is False

    service.close()
    assert service._closed is True
    assert service.barrier.calls == 2
