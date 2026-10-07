"""Native BM25 must use only the queried generation's corpus statistics."""
import json
from pathlib import Path
import subprocess
import sys

import apsw
import pytest

from codeintel.lab.retrieval import index_repository, query_repository, verify_packet


def test_same_active_snapshot_ranks_identically_after_opposite_histories(tmp_path):
    repo = tmp_path / 'source'
    repo.mkdir()
    def clear():
        for path in repo.iterdir():
            path.unlink()
    def current():
        clear()
        (repo / 'a.py').write_text('def first():\n    return "alpha"\n')
        (repo / 'b.py').write_text('def second():\n    return "beta"\n')
    for term in ('alpha', 'beta'):
        clear()
        for i in range(20):
            (repo / f'old_{i:02}.py').write_text(f'def old_{i:02}():\n    return "{term}"\n')
        index_repository(repo, tmp_path / term)
        current()
        index_repository(repo, tmp_path / term)
    index_repository(repo, tmp_path / 'clean')
    packets = []
    for state in ('alpha', 'beta', 'clean'):
        packet, _ = query_repository(repo, tmp_path / state, 'alpha beta', limit=1)
        verify_packet(repo, packet)
        packets.append(packet)
    assert len({p['snapshot_sha256'] for p in packets}) == 1
    assert packets[0] == packets[1] == packets[2]


def test_version_one_state_is_rejected_without_changing_bytes(tmp_path):
    repo, state = tmp_path / 'source', tmp_path / 'state'
    repo.mkdir()
    (repo / 'a.py').write_text('def marker():\n    return 1\n')
    index_repository(repo, state)
    db = state / 'db.sqlite'
    conn = apsw.Connection(str(db))
    conn.pragma('user_version', 1)
    conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    conn.close()
    before = db.read_bytes()
    result = subprocess.run([sys.executable, '-m', 'codeintel.lab.cli', 'query', '--repo', str(repo), '--state-dir', str(state), 'marker'], capture_output=True, timeout=10)
    assert result.returncode == 2 and not result.stdout
    error = json.loads(result.stderr)
    assert error['code'] == 'DerivedStateValidationError'
    assert 'new state directory' in error['recovery']
    assert db.read_bytes() == before
    check = apsw.Connection(str(db), flags=apsw.SQLITE_OPEN_READONLY)
    assert check.pragma('user_version') == 1
    check.close()

from codeintel.core.identity import canonical_hash, make_chunk_id, make_file_id
from codeintel.core.models import Chunk, FileRecord
from codeintel.service import create_default_service
from codeintel.storage import sqlite_store
from codeintel.storage.policy import (DerivedStateValidationError, generation_fts_name,
                                      create_generation_fts, validate_fts_coherence)
from codeintel.storage.production import ProductionSQLiteStore


def add_generation(store, repo, sequence, contents):
    generation = store.create_generation(repo, sequence, canonical_hash(repo + str(sequence)), metadata={'lifecycle_state': 'STAGING'})
    chunks = []
    for i, content in enumerate(contents):
        path = f'{repo}_{i}.py'
        fid = make_file_id(repo, path)
        span, hashed = (1, 0, 1, len(content.encode())), canonical_hash(content)
        store.add_files([FileRecord(fid, repo, generation.generation_id, path, hashed, len(content.encode()))])
        chunks.append(Chunk(make_chunk_id(fid, generation.generation_id, span, hashed), fid, generation.generation_id, path, span, content, hashed))
    store.index_chunks(chunks)
    store.update_generation_metadata(generation.generation_id, {'lifecycle_state': 'COMMITTED'})
    return generation, chunks


def fts_tables(store):
    import re
    return {row['name'] for row in store._conn.execute("SELECT name FROM sqlite_schema WHERE type='table'")
            if re.fullmatch(r'chunks_fts_[0-9a-f]{64}', row['name'])}


@pytest.mark.parametrize('invalid', ['', 'x"; DROP TABLE chunks; --', '../other', 'ñ', 'a' * 129, None])
def test_fts_identifier_rejects_unsafe_input(invalid):
    with pytest.raises(ValueError):
        generation_fts_name(invalid)


def test_empty_generation_owns_empty_fts_and_retry_delete_is_idempotent(tmp_path):
    path = str(tmp_path / 'db.sqlite')
    with ProductionSQLiteStore(path) as store:
        generation, _ = add_generation(store, 'repo', 1, [])
        store.activate_generation(generation.generation_id)
        assert fts_tables(store) == {generation_fts_name(generation.generation_id)}
        assert store.search('needle', generation_id=generation.generation_id) == []
        validate_fts_coherence(store._conn)
    with ProductionSQLiteStore(path) as store:
        store.delete_generation_data(generation.generation_id)
        store.delete_generation_data(generation.generation_id)
        assert not fts_tables(store)
        assert store.search('needle', generation_id=generation.generation_id) == []
        assert store.get_all_fts_chunk_ids(generation.generation_id) == []


def test_generation_and_fts_creation_roll_back_together_on_cancellation(tmp_path, monkeypatch):
    with ProductionSQLiteStore(str(tmp_path / 'db.sqlite')) as store:
        original = sqlite_store.create_generation_fts
        def interrupted(connection, generation):
            original(connection, generation)
            raise KeyboardInterrupt('cancel after virtual-table creation')
        monkeypatch.setattr(sqlite_store, 'create_generation_fts', interrupted)
        with pytest.raises(KeyboardInterrupt):
            store.create_generation('repo', 1, 'a' * 64)
        assert not store._conn.in_transaction
        assert not store.list_generations('repo') and not fts_tables(store)
        monkeypatch.setattr(sqlite_store, 'create_generation_fts', original)
        generation = store.create_generation('repo', 1, 'a' * 64)
        assert fts_tables(store) == {generation_fts_name(generation.generation_id)}


def test_delete_rollback_restores_fts_and_retry_removes_it(tmp_path):
    with ProductionSQLiteStore(str(tmp_path / 'db.sqlite')) as store:
        generation, chunks = add_generation(store, 'repo', 1, ['needle data'])
        store._conn.execute("CREATE TRIGGER reject_delete BEFORE DELETE ON generations BEGIN SELECT RAISE(ROLLBACK, 'delete rollback'); END")
        with pytest.raises(apsw.ConstraintError, match='delete rollback'):
            store.delete_generation_data(generation.generation_id)
        assert not store._conn.in_transaction
        assert store.search('needle', generation_id=generation.generation_id)[0][0].chunk_id == chunks[0].chunk_id
        validate_fts_coherence(store._conn)
        store._conn.execute('DROP TRIGGER reject_delete')
        store.delete_generation_data(generation.generation_id)
        assert not fts_tables(store)
        assert not list(store._conn.execute("SELECT name FROM sqlite_schema WHERE name LIKE 'chunks_fts_%'"))


@pytest.mark.parametrize('damage', ['missing', 'orphan', 'wrong-tokenizer', 'indexed-metadata'])
def test_open_rejects_generation_table_schema_damage(tmp_path, damage):
    db = tmp_path / 'db.sqlite'
    with ProductionSQLiteStore(str(db)) as store:
        generation, _ = add_generation(store, 'repo', 1, ['needle data'])
        table = generation_fts_name(generation.generation_id)
        if damage == 'orphan':
            create_generation_fts(store._conn, 'unknown_generation')
        else:
            store._conn.execute(f'DROP TABLE "{table}"')
            if damage != 'missing':
                tokenizer = 'unicode61' if damage == 'wrong-tokenizer' else 'trigram'
                modifier = '' if damage == 'indexed-metadata' else 'UNINDEXED'
                store._conn.execute(f'CREATE VIRTUAL TABLE "{table}" USING fts5(chunk_id {modifier}, file_id UNINDEXED, generation_id UNINDEXED, content, tokenize=\'{tokenizer}\')')
    before = db.read_bytes()
    with pytest.raises(DerivedStateValidationError):
        ProductionSQLiteStore(str(db))
    assert db.read_bytes() == before


def test_unscoped_search_round_robins_active_corpora_without_score_comparison(tmp_path):
    with ProductionSQLiteStore(str(tmp_path / 'db.sqlite')) as store:
        old, _ = add_generation(store, 'a', 1, ['needle only_inactive'])
        a, _ = add_generation(store, 'a', 2, ['needle alpha', 'needle alpha alpha'])
        b, _ = add_generation(store, 'b', 1, ['needle beta', 'needle beta beta'])
        store.activate_generation(old.generation_id)
        store.activate_generation(a.generation_id)
        store.activate_generation(b.generation_id)
        local_a = store.search('needle', generation_id=a.generation_id)
        local_b = store.search('needle', generation_id=b.generation_id)
        queries = []
        store._conn.set_exec_trace(lambda _c, sql, _bindings: (queries.append(sql), True)[1])
        try:
            merged = store.search('needle', limit=3)
            assert store.search('only_inactive') == []
            assert store.search('needle', generation_id='unknown') == []
        finally:
            store._conn.set_exec_trace(None)
        assert [row[0].chunk_id for row in merged] == [local_a[0][0].chunk_id, local_b[0][0].chunk_id, local_a[1][0].chunk_id]
        assert {row[0].generation_id for row in merged} == {a.generation_id, b.generation_id}
        assert all(row[0].generation_id == a.generation_id for row in local_a)
        assert not any(sql.lstrip().split()[0].upper() in {'CREATE', 'INSERT', 'UPDATE', 'DELETE', 'DROP'} for sql in queries)


def test_gc_keeps_leased_fts_then_removes_it_after_release(tmp_path):
    repo, state = tmp_path / 'source', tmp_path / 'state'
    repo.mkdir()
    path = repo / 'a.py'
    path.write_text('VALUE = 0\n')
    index_repository(repo, state)
    reader = create_default_service(str(repo), str(state))
    first = reader.barrier.active_generation.generation_id
    try:
        with create_default_service(str(repo), str(state)) as writer:
            for number in range(1, 6):
                path.write_text(f'VALUE = {number}\n')
                writer.reindex()
            assert generation_fts_name(first) in fts_tables(writer.graph_store)
            assert len(fts_tables(writer.graph_store)) == 4  # three retained + leased predecessor
            reader.close()
            report = writer.barrier._gc_generations()
            assert first in report['deleted'] and not report['errors']
            assert generation_fts_name(first) not in fts_tables(writer.graph_store)
            assert len(fts_tables(writer.graph_store)) == 3
            assert not writer.barrier._gc_generations()['deleted']
            validate_fts_coherence(writer.graph_store._conn)
    finally:
        reader.close()


def test_storage_policy_change_invalidates_previous_build_identity():
    from codeintel.freshness.barrier import compute_index_build_fingerprint
    from tests.legacy_build_fingerprint import legacy_build_fingerprint
    assert compute_index_build_fingerprint() != legacy_build_fingerprint(schema_version='1.2.0')
