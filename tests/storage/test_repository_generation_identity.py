"""New persistent generation identities bind repo, sequence and full snapshot."""
import hashlib
import json

import pytest

from codeintel.core.identity import make_repo_id
from tests.legacy_generation_fixture import legacy_generation_id
from codeintel.lab.retrieval import index_repository, query_repository, verify_packet
from codeintel.service import create_default_service
from codeintel.storage.policy import generation_fts_name, validate_fts_coherence
from codeintel.storage.production import ProductionSQLiteStore
from codeintel.storage.sqlite_store import SQLiteStore


def expected_id(repo, sequence, snapshot):
    payload = json.dumps(['codeintel-generation-v2', repo, sequence, snapshot],
                         ensure_ascii=True, separators=(',', ':'))
    digest = hashlib.sha256(payload.encode('utf-8')).hexdigest()
    return f'gen_{sequence:06d}_{digest}'


@pytest.mark.parametrize('repo,sequence,snapshot', [
    ('repository-a', 1, 'a' * 64),
    ('repository-b', 1, 'a' * 64),
    ('repository-a', 2, 'a' * 64),
    ('repository-a', 1, 'a' * 12 + 'b' * 52),
    ('repository-ñ-"\\', 1000000, 'c' * 64),
])
def test_identity_matches_unambiguous_full_digest_contract(repo, sequence, snapshot):
    assert SQLiteStore._new_generation_id(repo, sequence, snapshot) == expected_id(repo, sequence, snapshot)


def test_shared_store_keeps_same_sequence_and_snapshot_separate_by_repository(tmp_path):
    with ProductionSQLiteStore(str(tmp_path / 'db.sqlite')) as store:
        a = store.create_generation('repository-a', 1, 'a' * 64)
        b = store.create_generation('repository-b', 1, 'a' * 64)
        assert a.generation_id != b.generation_id
        assert [g.generation_id for g in store.list_generations('repository-a')] == [a.generation_id]
        assert [g.generation_id for g in store.list_generations('repository-b')] == [b.generation_id]
        validate_fts_coherence(store._conn)


def test_full_snapshot_suffix_changes_identity(tmp_path):
    with ProductionSQLiteStore(str(tmp_path / 'db.sqlite')) as store:
        first = store.create_generation('repository', 1, 'a' * 64)
        second = store.create_generation('repository', 1, 'a' * 12 + 'b' * 52)
        assert first.generation_id != second.generation_id


def test_identical_repositories_share_state_without_cross_repository_gc(tmp_path):
    repos = [tmp_path / 'repo-a', tmp_path / 'repo-b']
    state = tmp_path / 'state'
    for repo in repos:
        repo.mkdir()
        (repo / 'source.py').write_text('def target():\n    return 0\n')
        index_repository(repo, state)
    reader_a = create_default_service(str(repos[0]), str(state))
    try:
        first_a = reader_a.barrier.active_generation.generation_id
        with create_default_service(str(repos[1]), str(state)) as reader_b:
            first_b = reader_b.barrier.active_generation.generation_id
            assert first_a != first_b
            with create_default_service(str(repos[0]), str(state)) as writer:
                for number in range(1, 6):
                    (repos[0] / 'source.py').write_text(f'def target():\n    return {number}\n')
                    writer.reindex()
                assert writer.graph_store.get_active_generation(reader_b.repo_id).generation_id == first_b
                assert first_a in {g.generation_id for g in writer.graph_store.list_generations(writer.repo_id)}
                reader_a.close()
                report = writer.barrier._gc_generations()
                assert first_a in report['deleted'] and first_b not in report['deleted']
                assert not report['errors']
                table = generation_fts_name(first_b)
                assert writer.graph_store._conn.execute('SELECT name FROM sqlite_schema WHERE name=?', (table,)).fetchone()
                validate_fts_coherence(writer.graph_store._conn)
            packet, _ = query_repository(repos[1], state, 'target')
            verify_packet(repos[1], packet)
            assert packet['selected']
    finally:
        reader_a.close()


def test_existing_legacy_ids_remain_opaque_and_readable(tmp_path, monkeypatch):
    repo, state = tmp_path / 'source', tmp_path / 'state'
    repo.mkdir()
    file = repo / 'source.py'
    file.write_text('def target():\n    return 1\n')
    with monkeypatch.context() as patch:
        patch.setattr(SQLiteStore, '_new_generation_id', staticmethod(
            lambda repo_id, sequence, snapshot_hash: legacy_generation_id(sequence, snapshot_hash)))
        index_repository(repo, state)
    with create_default_service(str(repo), str(state)) as service:
        old = service.barrier.active_generation
        assert old.generation_id == legacy_generation_id(old.sequence, old.snapshot_hash)
        packet, _ = query_repository(repo, state, 'target')
        verify_packet(repo, packet)
        file.write_text('def target():\n    return 2\n')
        service.reindex()
        new = service.barrier.active_generation
        assert new.generation_id == expected_id(service.repo_id, new.sequence, new.snapshot_hash)
        assert old.generation_id in {g.generation_id for g in service.graph_store.list_generations(service.repo_id)}


def test_canonical_root_alias_reuses_existing_generation(tmp_path):
    repo, state = tmp_path / 'source', tmp_path / 'state'
    repo.mkdir()
    (repo / 'sub').mkdir()
    (repo / 'source.py').write_text('def target():\n    return 1\n')
    alias = repo / 'sub' / '..'
    assert make_repo_id(str(repo)) == make_repo_id(str(alias))
    index_repository(repo, state)
    with create_default_service(str(repo), str(state)) as original:
        before = original.barrier.active_generation.generation_id
    packet, _ = query_repository(alias, state, 'target')
    verify_packet(alias, packet)
    with create_default_service(str(alias), str(state)) as equivalent:
        assert equivalent.barrier.active_generation.generation_id == before
