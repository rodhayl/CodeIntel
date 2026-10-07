"""Real SQLite checks for the production transaction method boundary.

The SQL transaction/exception protocol is isolated from schema/bootstrap policy.
Full APSW integration must be validated separately in the complete repository.
"""
from __future__ import annotations

import asyncio
import sqlite3
import threading

import pytest

from codeintel.storage.production import ProductionSQLiteStore


@pytest.fixture
def store():
    value = object.__new__(ProductionSQLiteStore)
    value._lock = threading.RLock()
    value._closed = False
    value._conn = sqlite3.connect(':memory:', isolation_level=None)
    value._conn.row_factory = sqlite3.Row
    value._conn.execute('CREATE TABLE writes (value TEXT)')
    value._conn.execute("INSERT INTO writes VALUES ('before')")
    try:
        yield value
    finally:
        value._conn.close()


@pytest.mark.parametrize('error_type', [KeyboardInterrupt, SystemExit, asyncio.CancelledError])
def test_batch_cancellation_rolls_back_and_releases_transaction(store, error_type):
    error = error_type('cancel after first row')

    def operation():
        store._conn.execute("INSERT INTO writes VALUES ('partial')")
        raise error

    with pytest.raises(error_type) as caught:
        store._atomic_batch('files', operation)
    assert caught.value is error
    assert not store._conn.in_transaction
    assert [row[0] for row in store._conn.execute('SELECT value FROM writes')] == ['before']
    assert store._atomic_batch('files', lambda: 17) == 17


def test_successful_batch_preserves_result_and_rows(store):
    def operation():
        store._conn.execute("INSERT INTO writes VALUES ('complete')")
        return 17

    assert store._atomic_batch('files', operation) == 17
    assert not store._conn.in_transaction
    assert [row[0] for row in store._conn.execute('SELECT value FROM writes')] == ['before', 'complete']


def test_regular_exception_still_rolls_back(store):
    def operation():
        store._conn.execute("INSERT INTO writes VALUES ('partial')")
        raise ValueError('invalid row')

    with pytest.raises(ValueError, match='invalid row'):
        store._atomic_batch('files', operation)
    assert not store._conn.in_transaction
    assert [row[0] for row in store._conn.execute('SELECT value FROM writes')] == ['before']


class _InterruptingCursor(sqlite3.Cursor):
    def execute(self, sql, parameters=()):
        result = super().execute(sql, parameters)
        if sql.startswith('UPDATE generations SET is_active = 0'):
            raise KeyboardInterrupt('activation cancelled after deactivation')
        return result


class _InterruptingConnection(sqlite3.Connection):
    def cursor(self, *args, **kwargs):
        return super().cursor(factory=_InterruptingCursor)


def test_activation_cancellation_restores_old_generation_and_unlocks():
    value = object.__new__(ProductionSQLiteStore)
    value._lock = threading.RLock()
    conn = sqlite3.connect(':memory:', isolation_level=None, factory=_InterruptingConnection)
    conn.row_factory = sqlite3.Row
    value._conn = conn
    try:
        conn.execute('CREATE TABLE generations (generation_id TEXT, repo_id TEXT, metadata_json TEXT, is_active INTEGER, build_fingerprint TEXT)')
        conn.executemany(
            'INSERT INTO generations (generation_id, repo_id, metadata_json, is_active) VALUES (?, ?, ?, ?)',
            [('gen_old', 'repo', '{"lifecycle_state":"COMMITTED"}', 1),
             ('gen_new', 'repo', '{"lifecycle_state":"COMMITTED"}', 0)],
        )
        with pytest.raises(KeyboardInterrupt, match='activation cancelled'):
            value.activate_generation('gen_new')
        assert not conn.in_transaction
        rows = conn.execute('SELECT generation_id, is_active FROM generations ORDER BY generation_id').fetchall()
        assert [tuple(row) for row in rows] == [('gen_new', 0), ('gen_old', 1)]
    finally:
        conn.close()
