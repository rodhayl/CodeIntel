"""Production policy must survive inherited generation persistence/read methods."""
from __future__ import annotations

import json
import sqlite3
import threading

import pytest

from codeintel.storage.production import MAX_GENERATION_METADATA_BYTES, ProductionSQLiteStore
from codeintel.storage.sqlite_store import SCHEMA_SQL


@pytest.fixture
def store():
    value = object.__new__(ProductionSQLiteStore)
    value._lock = threading.RLock()
    value._closed = False
    value._conn = sqlite3.connect(':memory:', isolation_level=None)
    value._conn.row_factory = lambda cursor, row: {
        column[0]: row[index] for index, column in enumerate(cursor.description)
    }
    value._conn.executescript(SCHEMA_SQL)
    try:
        yield value
    finally:
        value._conn.close()


@pytest.mark.parametrize('text', ['x' * (MAX_GENERATION_METADATA_BYTES - 8), '\u03bb' * 15000], ids=['exact-byte-limit', 'unicode-expansion'])
def test_created_metadata_uses_admitted_canonical_encoding(store, text):
    metadata = {'x': text}
    generation = store.create_generation('repo', 1, 'a' * 64, metadata=metadata)
    row = store._conn.execute('SELECT metadata_json FROM generations').fetchone()
    expected = json.dumps(metadata, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    assert row['metadata_json'] == expected
    assert len(row['metadata_json'].encode('utf-8')) <= MAX_GENERATION_METADATA_BYTES
    assert store._load_metadata(row['metadata_json']) == generation.metadata == metadata


def test_fingerprint_injection_cannot_exceed_write_budget(store):
    with pytest.raises(ValueError):
        store.create_generation('repo', 1, 'a' * 64, build_fingerprint='x' * MAX_GENERATION_METADATA_BYTES)
    assert store._conn.execute('SELECT COUNT(*) AS n FROM generations').fetchone()['n'] == 0
    assert not store._conn.in_transaction


@pytest.mark.parametrize('reader', ['get_active_generation', 'list_generations'])
@pytest.mark.parametrize('raw', ['{"x":1,"x":2}', '{"x":1e999}', '[]', ' ' * MAX_GENERATION_METADATA_BYTES + '{}'], ids=['duplicate-key', 'nonfinite', 'wrong-shape', 'raw-overflow'])
def test_inherited_readers_apply_production_metadata_policy(store, reader, raw):
    store._conn.execute(
        'INSERT INTO generations (generation_id,repo_id,sequence,snapshot_hash,is_active,metadata_json) VALUES (?,?,?,?,?,?)',
        ('gen_old', 'repo', 1, 'a' * 64, 1, raw),
    )
    with pytest.raises(RuntimeError, match='strict JSON'):
        getattr(store, reader)('repo')


@pytest.mark.parametrize('reader', ['get_active_generation', 'list_generations'])
def test_fallback_fingerprint_is_admitted_before_returning_metadata(store, reader):
    store._conn.execute(
        'INSERT INTO generations (generation_id,repo_id,sequence,snapshot_hash,is_active,build_fingerprint,metadata_json) VALUES (?,?,?,?,?,?,?)',
        ('gen_old', 'repo', 1, 'a' * 64, 1, 'x' * MAX_GENERATION_METADATA_BYTES, '{}'),
    )
    with pytest.raises(RuntimeError, match='strict JSON'):
        getattr(store, reader)('repo')


def test_generation_roundtrip_and_activation_positive_control(store):
    metadata = {'lifecycle_state': 'COMMITTED', 'nested': [1, '\u03bb', None]}
    created = store.create_generation('repo', 1, 'a' * 64, build_fingerprint='build-v1', metadata=metadata)
    assert store.get_active_generation('repo') is None
    store.activate_generation(created.generation_id)
    active = store.get_active_generation('repo')
    assert active is not None and active.generation_id == created.generation_id
    assert active.metadata == {**metadata, 'build_fingerprint': 'build-v1'}
    assert store.list_generations('repo') == [active]
    assert store.list_generations('missing') == []


@pytest.mark.parametrize('operation', ['activate', 'update'])
def test_corrupt_fingerprint_cannot_mutate_or_activate_generation(store, operation):
    old = store.create_generation('repo', 1, 'a' * 64, metadata={'lifecycle_state': 'COMMITTED'})
    new = store.create_generation('repo', 2, 'b' * 64, metadata={'lifecycle_state': 'COMMITTED'})
    store.activate_generation(old.generation_id)
    store._conn.execute('UPDATE generations SET build_fingerprint = ? WHERE generation_id = ?',
                        ('x' * MAX_GENERATION_METADATA_BYTES, new.generation_id))
    before = store._conn.execute('SELECT * FROM generations ORDER BY sequence').fetchall()
    with pytest.raises(RuntimeError, match='strict JSON'):
        if operation == 'activate':
            store.activate_generation(new.generation_id)
        else:
            store.update_generation_metadata(new.generation_id, {'extra': 1})
    assert not store._conn.in_transaction
    assert store._conn.execute('SELECT * FROM generations ORDER BY sequence').fetchall() == before


def test_base_store_legacy_codec_remains_compatible(store):
    from codeintel.storage.sqlite_store import SQLiteStore
    legacy = object.__new__(SQLiteStore)
    legacy._lock = store._lock
    legacy._conn = store._conn
    created = legacy.create_generation('legacy', 1, 'short', build_fingerprint='legacy-fp', metadata={1: 'value'})
    assert created.metadata == {1: 'value', 'build_fingerprint': 'legacy-fp'}
    assert legacy.list_generations('legacy')[0].metadata == {'1': 'value', 'build_fingerprint': 'legacy-fp'}
