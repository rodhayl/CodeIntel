"""The literal baseline scans all chunks while retaining only its top candidates."""
import weakref

from codeintel.lab.retrieval import index_repository, query_repository, verify_packet
from codeintel.storage.production import ProductionSQLiteStore


def test_literal_baseline_does_not_materialize_all_chunk_ids(tmp_path, monkeypatch):
    repo, state = tmp_path / 'repo', tmp_path / 'state'
    repo.mkdir()
    for index in range(45):
        (repo / f'module_{index:02}.py').write_text(f'def shared_{index}():\n    return {index}\n')
    index_repository(repo, state)
    original = ProductionSQLiteStore.get_all_chunk_ids
    calls = []
    def all_ids(self, generation_id):
        calls.append(generation_id)
        return original(self, generation_id)
    monkeypatch.setattr(ProductionSQLiteStore, 'get_all_chunk_ids', all_ids)
    materialized = []
    peaks = []
    original_chunk = ProductionSQLiteStore._row_to_chunk
    def chunk_row(row):
        chunk = original_chunk(row)
        materialized.append(weakref.ref(chunk))
        peaks.append(sum(reference() is not None for reference in materialized))
        return chunk
    monkeypatch.setattr(ProductionSQLiteStore, '_row_to_chunk', staticmethod(chunk_row))
    packet, _ = query_repository(repo, state, 'shared', baseline=True, max_bytes=8192, limit=20)
    verify_packet(repo, packet)
    assert not calls, 'Literal acquisition must stream chunks rather than materialize every chunk ID'
    assert packet['selected'][0]['path'] == 'module_00.py'
    assert len(materialized) >= 45, 'The complete baseline corpus is still scanned'
    assert max(peaks) <= 34, 'Only 32 candidates and transient stream rows should remain live'
