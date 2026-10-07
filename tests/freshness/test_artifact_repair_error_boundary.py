"""Only recognized derived artifacts justify an automatic immutable rebuild."""
import pytest

from codeintel.service.production import create_default_service


def test_unexpected_artifact_inspection_failure_does_not_rebuild(tmp_path, monkeypatch):
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / 'a.py').write_text('def marker():\n    return 1\n')
    with create_default_service(str(repo), str(tmp_path / 'state')) as service:
        service.reindex()
        before = service.barrier.active_generation.generation_id
        original = service.graph_store.get_all_chunk_ids
        calls = 0
        def broken_once(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError('unexpected programmer defect')
            return original(*args, **kwargs)
        monkeypatch.setattr(service.graph_store, 'get_all_chunk_ids', broken_once)
        with pytest.raises(RuntimeError, match='unexpected programmer defect'):
            service.reindex()
        assert service.barrier.active_generation.generation_id == before
        assert [g.generation_id for g in service.graph_store.list_generations(service.repo_id)] == [before]
        assert calls == 1
        assert service.search('marker')
