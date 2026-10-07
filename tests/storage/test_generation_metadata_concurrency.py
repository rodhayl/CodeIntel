"""Real WAL connections exercise the read/modify/write serialization boundary."""
from __future__ import annotations

import concurrent.futures
import json
import sqlite3
import threading

import pytest

from codeintel.storage.production import ProductionSQLiteStore
from codeintel.storage.sqlite_store import SCHEMA_SQL


def make_store(path, *, connection_type=sqlite3.Connection):
    store = object.__new__(ProductionSQLiteStore)
    store._lock = threading.RLock()
    store._closed = False
    store._conn = sqlite3.connect(
        path, isolation_level=None, check_same_thread=False,
        timeout=5, factory=connection_type,
    )
    store._conn.row_factory = lambda cur, row: {
        column[0]: row[index] for index, column in enumerate(cur.description)
    }
    return store


def test_two_connections_do_not_lose_disjoint_metadata_updates(tmp_path):
    first_read = threading.Event()
    resume_first = threading.Event()

    class PausingCursor(sqlite3.Cursor):
        pause = False

        def execute(self, sql, parameters=()):
            self.pause = sql.startswith('SELECT metadata_json, build_fingerprint')
            return super().execute(sql, parameters)

        def fetchone(self):
            row = super().fetchone()
            if self.pause:
                self.pause = False
                first_read.set()
                assert resume_first.wait(5), 'second writer did not reach synchronization point'
            return row

    class PausingConnection(sqlite3.Connection):
        def cursor(self, *args, **kwargs):
            return super().cursor(factory=PausingCursor)

    path = str(tmp_path / 'state.sqlite')
    first = make_store(path, connection_type=PausingConnection)
    second = make_store(path)
    try:
        first._conn.executescript(SCHEMA_SQL)
        generation = first.create_generation('repo', 1, 'a' * 64, metadata={'original': 1})
        # If writer A owns a transaction, B signals before its real SQLite BEGIN
        # blocks. Without a transaction B can commit first, reproducing lost data.
        second._conn.set_trace_callback(
            lambda sql: resume_first.set() if sql == 'BEGIN IMMEDIATE' else None
        )

        def write_first():
            first.update_generation_metadata(generation.generation_id, {'first': 2})

        def write_second():
            assert first_read.wait(5), 'first writer did not finish its SELECT'
            try:
                second.update_generation_metadata(generation.generation_id, {'second': 3})
            finally:
                resume_first.set()

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            left = pool.submit(write_first)
            right = pool.submit(write_second)
            left.result(timeout=10)
            right.result(timeout=10)
        raw = first._conn.execute('SELECT metadata_json FROM generations').fetchone()['metadata_json']
        assert json.loads(raw) == {'original': 1, 'first': 2, 'second': 3}
        assert not first._conn.in_transaction
        assert not second._conn.in_transaction
    finally:
        resume_first.set()
        first._conn.close()
        second._conn.close()


def test_failed_merge_rolls_back_and_connection_remains_usable(tmp_path):
    store = make_store(str(tmp_path / 'state.sqlite'))
    try:
        store._conn.executescript(SCHEMA_SQL)
        generation = store.create_generation('repo', 1, 'a' * 64, metadata={'old': 'x' * 40000})
        before = store._conn.execute('SELECT metadata_json FROM generations').fetchone()
        with pytest.raises(ValueError):
            store.update_generation_metadata(generation.generation_id, {'new': 'y' * 40000})
        assert not store._conn.in_transaction
        assert store._conn.execute('SELECT metadata_json FROM generations').fetchone() == before
        store.update_generation_metadata(generation.generation_id, {'ok': True})
        assert store.list_generations('repo')[0].metadata['ok'] is True
    finally:
        store._conn.close()


def test_cancelled_metadata_write_restores_old_record(tmp_path):
    class InterruptingCursor(sqlite3.Cursor):
        def execute(self, sql, parameters=()):
            result = super().execute(sql, parameters)
            if sql.startswith('UPDATE generations SET metadata_json'):
                raise KeyboardInterrupt('metadata update cancelled after SQL write')
            return result

    class InterruptingConnection(sqlite3.Connection):
        def cursor(self, *args, **kwargs):
            return super().cursor(factory=InterruptingCursor)

    store = make_store(str(tmp_path / 'state.sqlite'), connection_type=InterruptingConnection)
    try:
        store._conn.executescript(SCHEMA_SQL)
        generation = store.create_generation('repo', 1, 'a' * 64, metadata={'old': 1})
        before = store._conn.execute('SELECT metadata_json FROM generations').fetchone()
        with pytest.raises(KeyboardInterrupt, match='cancelled'):
            store.update_generation_metadata(generation.generation_id, {'new': 2})
        assert not store._conn.in_transaction
        assert store._conn.execute('SELECT metadata_json FROM generations').fetchone() == before
    finally:
        store._conn.close()


def test_missing_generation_releases_metadata_write_transaction(tmp_path):
    store = make_store(str(tmp_path / 'state.sqlite'))
    try:
        store._conn.executescript(SCHEMA_SQL)
        with pytest.raises(ValueError, match='does not exist'):
            store.update_generation_metadata('gen_missing', {'new': 2})
        assert not store._conn.in_transaction
    finally:
        store._conn.close()
