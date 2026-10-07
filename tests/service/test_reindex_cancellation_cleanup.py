from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import codeintel.service.domain_service as domain_module
from codeintel.core.models import Generation
from codeintel.freshness.barrier import StagingResult
from codeintel.service.domain_service import DomainService


class _Barrier:
    def __init__(self, staging):
        self.staging = staging
        self.aborted = []
        self.release_count = 0

    def create_staging_generation(self):
        return self.staging

    def abort_staging_generation(self, generation):
        self.aborted.append(generation.generation_id)

    def release_rebuild_lock(self):
        self.release_count += 1


@pytest.mark.parametrize("exc_type", [KeyboardInterrupt, asyncio.CancelledError])
@pytest.mark.parametrize("cancel_at", ["source-bytes", "parse"])
def test_reindex_aborts_staging_generation_on_cancellation(tmp_path, monkeypatch, exc_type, cancel_at):
    (tmp_path / "a.py").write_text("value = 1\n")
    generation = Generation(
        generation_id="gen_stage",
        repo_id="repo",
        sequence=1,
        snapshot_hash="snapshot",
    )
    staging = StagingResult(
        generation=generation,
        current_hashes={"a.py": "a" * 64},
        reused_existing=False,
    )
    barrier = _Barrier(staging)
    service = object.__new__(DomainService)
    service.repo_root = str(tmp_path)
    service.repo_id = "repo"
    service.state_dir = str(tmp_path / "state")
    service.barrier = barrier
    service.graph_store = SimpleNamespace(add_files=lambda _files: None)
    service.lexical_index = SimpleNamespace()
    service.generation_store = SimpleNamespace()
    service.merger = SimpleNamespace(
        parse_repository=lambda **_kwargs: (_ for _ in ()).throw(
            exc_type("cancel reindex")
        )
    )
    if cancel_at == "source-bytes":
        def cancelled_read(*_args):
            raise exc_type("cancel reindex")
        monkeypatch.setattr(domain_module, "read_generation_source_bytes", cancelled_read)

    with pytest.raises(exc_type, match="cancel reindex"):
        service.reindex()

    assert barrier.aborted == ["gen_stage"]
    assert barrier.release_count == 1
