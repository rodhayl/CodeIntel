"""Metadata trust-boundary checks; SQLite fixture isolates activation SQL policy."""
from __future__ import annotations
import json
import sqlite3
import threading
import pytest
from codeintel.storage.production import MAX_GENERATION_METADATA_BYTES, ProductionSQLiteStore


@pytest.mark.parametrize('raw', [
    '{"lifecycle_state":"STAGING","lifecycle_state":"COMMITTED"}',
    '{"nested":{"identity":1,"identity":2}}',
    '{"x":1,"\\u0078":2}',
])
def test_stored_metadata_rejects_ambiguous_duplicate_keys(raw):
    with pytest.raises(RuntimeError, match='strict JSON'):
        ProductionSQLiteStore._load_metadata(raw)


@pytest.mark.parametrize('raw', [False, 0, [], {}, b'', b'{}'])
def test_stored_metadata_rejects_non_text_even_when_falsy(raw):
    with pytest.raises(RuntimeError):
        ProductionSQLiteStore._load_metadata(raw)


def test_raw_whitespace_budget_is_enforced_before_json_parser(monkeypatch):
    raw = ' ' * MAX_GENERATION_METADATA_BYTES + '{}'
    calls = []
    original = json.loads
    def loads(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(json, 'loads', loads)
    with pytest.raises(RuntimeError):
        ProductionSQLiteStore._load_metadata(raw)
    assert not calls


@pytest.mark.parametrize('raw', ['{"n":1e999}', '{"n":{"value":-1e999}}', '[' * 1200 + '0' + ']' * 1200])
def test_stored_corruption_uses_fail_closed_runtime_error(raw):
    with pytest.raises(RuntimeError, match='strict JSON'):
        ProductionSQLiteStore._load_metadata(raw)


@pytest.mark.parametrize('metadata', [{1: 'value'}, {'nested': {2: 3}}, {'coordinates': (1, 2)}])
def test_write_metadata_rejects_python_json_coercions(metadata):
    with pytest.raises(ValueError):
        ProductionSQLiteStore._validated_metadata(metadata, 'metadata')


def test_validated_metadata_is_detached_from_caller_mutation():
    metadata = {'nested': {'values': [1, 2]}}
    result = ProductionSQLiteStore._validated_metadata(metadata, 'metadata')
    metadata['nested']['values'].append(3)
    assert result == {'nested': {'values': [1, 2]}}


def test_shared_dag_is_bounded_before_serialization(monkeypatch):
    value = 'x'
    for _ in range(17):
        value = [value, value]
    calls = []
    original = json.dumps
    def dumps(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(json, 'dumps', dumps)
    with pytest.raises(ValueError):
        ProductionSQLiteStore._validated_metadata({'value': value}, 'metadata')
    assert not calls


def test_huge_string_is_bounded_before_serialization(monkeypatch):
    calls = []
    original = json.dumps
    def dumps(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(json, 'dumps', dumps)
    with pytest.raises(ValueError):
        ProductionSQLiteStore._validated_metadata({'value': 'x' * (MAX_GENERATION_METADATA_BYTES + 1)}, 'metadata')
    assert not calls


def test_native_json_unicode_and_small_shared_values_remain_usable():
    shared = {'name': '\u03bb', 'ok': True, 'missing': None, 'ratio': 0.5}
    value = {'a': shared, 'b': shared, 'count': 2}
    clean = ProductionSQLiteStore._validated_metadata(value, 'metadata')
    assert clean == value
    assert ProductionSQLiteStore._load_metadata(json.dumps(clean)) == value


def test_exact_compact_byte_boundary_remains_valid():
    value = {'x': 'a' * (MAX_GENERATION_METADATA_BYTES - 8)}
    raw = json.dumps(value, separators=(',', ':'))
    assert len(raw.encode('utf-8')) == MAX_GENERATION_METADATA_BYTES
    assert ProductionSQLiteStore._validated_metadata(value, 'metadata') == value
    assert ProductionSQLiteStore._load_metadata(raw) == value


def test_legacy_absent_metadata_remains_supported():
    assert ProductionSQLiteStore._load_metadata(None) == {}
    assert ProductionSQLiteStore._load_metadata('') == {}
    assert ProductionSQLiteStore._validated_metadata(None, 'metadata') == {}


def test_cycle_is_rejected():
    value = {}
    value['self'] = value
    with pytest.raises(ValueError):
        ProductionSQLiteStore._validated_metadata(value, 'metadata')


def test_duplicate_lifecycle_cannot_activate_generation():
    store = object.__new__(ProductionSQLiteStore)
    store._lock = threading.RLock()
    conn = sqlite3.connect(':memory:', isolation_level=None)
    conn.row_factory = sqlite3.Row
    store._conn = conn
    try:
        conn.execute('CREATE TABLE generations (generation_id TEXT, repo_id TEXT, metadata_json TEXT, is_active INTEGER, build_fingerprint TEXT)')
        conn.executemany('INSERT INTO generations (generation_id, repo_id, metadata_json, is_active) VALUES (?, ?, ?, ?)', [
            ('gen_old', 'repo', '{"lifecycle_state":"COMMITTED"}', 1),
            ('gen_new', 'repo', '{"lifecycle_state":"STAGING","lifecycle_state":"COMMITTED"}', 0),
        ])
        with pytest.raises(RuntimeError):
            store.activate_generation('gen_new')
        assert not conn.in_transaction
        assert [tuple(row) for row in conn.execute('SELECT generation_id, is_active FROM generations ORDER BY generation_id')] == [('gen_new', 0), ('gen_old', 1)]
    finally:
        conn.close()
