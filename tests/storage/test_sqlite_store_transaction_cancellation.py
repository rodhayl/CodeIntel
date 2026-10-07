from __future__ import annotations

import sqlite3
import threading

import pytest

from codeintel.core.models import Chunk
from codeintel.storage.sqlite_store import SQLiteStore
from codeintel.storage.policy import generation_fts_name


def _bare_store(conn) -> SQLiteStore:
    store = object.__new__(SQLiteStore)
    store._lock = threading.RLock()
    store._conn = conn
    return store


def _row_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


def test_delete_generation_preserves_engine_rollback_error():
    conn = _row_connection()
    conn.executescript(
        """
        CREATE TABLE generations(generation_id TEXT PRIMARY KEY);
        CREATE TABLE files(generation_id TEXT);
        CREATE TABLE entities(generation_id TEXT);
        CREATE TABLE relations(generation_id TEXT);
        CREATE TABLE chunks(generation_id TEXT);
        INSERT INTO generations VALUES('gen_1');
        CREATE TRIGGER rollback_delete
        BEFORE DELETE ON generations
        BEGIN SELECT RAISE(ROLLBACK, 'delete engine rollback'); END;
        """
    )
    store = _bare_store(conn)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="delete engine rollback"):
            store.delete_generation_data("gen_1")
        assert not conn.in_transaction
        assert conn.execute("SELECT COUNT(*) FROM generations").fetchone()[0] == 1
        assert conn.execute("SELECT 1").fetchone()[0] == 1
    finally:
        conn.close()


class _InterruptingCursor:
    def __init__(self, cursor):
        self._cursor = cursor

    def execute(self, sql, params=()):
        return self._cursor.execute(sql, params)

    def executemany(self, sql, params):
        if "INSERT OR REPLACE INTO chunks\n" in sql and "chunks_fts" not in sql:
            raise KeyboardInterrupt("index cancelled")
        return self._cursor.executemany(sql, params)

    def __getattr__(self, name):
        return getattr(self._cursor, name)


class _InterruptingConnection:
    def __init__(self, connection):
        self._connection = connection

    @property
    def in_transaction(self):
        return self._connection.in_transaction

    def cursor(self):
        return _InterruptingCursor(self._connection.cursor())

    def __getattr__(self, name):
        return getattr(self._connection, name)


def test_index_chunks_rolls_back_on_keyboard_interrupt():
    conn = _row_connection()
    conn.executescript(
        """
        CREATE TABLE chunks(
            chunk_id TEXT, file_id TEXT, generation_id TEXT, rel_path TEXT,
            start_line INTEGER, start_col INTEGER, end_line INTEGER, end_col INTEGER,
            content TEXT, content_hash TEXT, entity_ids_json TEXT
        );
        CREATE TABLE chunks_fts(
            chunk_id TEXT, file_id TEXT, generation_id TEXT, content TEXT
        );
        INSERT INTO chunks VALUES(
            'old', 'file_1', 'gen_1', 'a.py', 1, 0, 1, 3, 'old', 'hash', '[]'
        );
        INSERT INTO chunks_fts VALUES('old', 'file_1', 'gen_1', 'old');
        """.replace("chunks_fts", generation_fts_name("gen_1"))
    )
    store = _bare_store(_InterruptingConnection(conn))
    chunk = Chunk(
        chunk_id="new",
        file_id="file_1",
        generation_id="gen_1",
        rel_path="a.py",
        span=(1, 0, 1, 3),
        content="new",
        content_hash="hash",
        entity_ids=[],
    )
    try:
        with pytest.raises(KeyboardInterrupt, match="index cancelled"):
            store.index_chunks([chunk])
        assert not conn.in_transaction
        assert conn.execute("SELECT chunk_id FROM chunks").fetchone()[0] == "old"
        assert conn.execute(f'SELECT chunk_id FROM "{generation_fts_name("gen_1")}"').fetchone()[0] == "old"
    finally:
        conn.close()
