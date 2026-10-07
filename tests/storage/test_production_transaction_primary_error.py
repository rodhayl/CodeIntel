from __future__ import annotations

import sqlite3
import threading

import pytest

from codeintel.storage.production import ProductionSQLiteStore


def _bare_store() -> ProductionSQLiteStore:
    store = object.__new__(ProductionSQLiteStore)
    store._lock = threading.RLock()
    store._closed = False
    store._conn = sqlite3.connect(":memory:", isolation_level=None)
    store._conn.row_factory = sqlite3.Row
    return store


def test_metadata_update_preserves_engine_rollback_error():
    store = _bare_store()
    try:
        store._conn.executescript(
            """
            CREATE TABLE generations(
                generation_id TEXT PRIMARY KEY,
                repo_id TEXT,
                metadata_json TEXT,
                build_fingerprint TEXT
            );
            INSERT INTO generations VALUES('gen_1', 'repo', '{}', NULL);
            CREATE TRIGGER rollback_metadata
            BEFORE UPDATE OF metadata_json ON generations
            BEGIN SELECT RAISE(ROLLBACK, 'metadata engine rollback'); END;
            """
        )
        with pytest.raises(sqlite3.IntegrityError, match="metadata engine rollback"):
            store.update_generation_metadata("gen_1", {"state": "updated"})
        assert not store._conn.in_transaction
        assert store._conn.execute("SELECT 1").fetchone()[0] == 1
    finally:
        store.close()


def test_activation_preserves_engine_rollback_error():
    store = _bare_store()
    try:
        store._conn.executescript(
            """
            CREATE TABLE generations(
                generation_id TEXT PRIMARY KEY,
                repo_id TEXT,
                metadata_json TEXT,
                build_fingerprint TEXT,
                is_active INTEGER
            );
            INSERT INTO generations VALUES(
                'gen_1', 'repo', '{"lifecycle_state":"COMMITTED"}', NULL, 0
            );
            CREATE TRIGGER rollback_activation
            BEFORE UPDATE OF is_active ON generations
            BEGIN SELECT RAISE(ROLLBACK, 'activation engine rollback'); END;
            """
        )
        with pytest.raises(sqlite3.IntegrityError, match="activation engine rollback"):
            store.activate_generation("gen_1")
        assert not store._conn.in_transaction
        assert store._conn.execute("SELECT 1").fetchone()[0] == 1
    finally:
        store.close()


def test_atomic_batch_preserves_engine_rollback_error():
    store = _bare_store()
    try:
        store._conn.executescript(
            """
            CREATE TABLE payload(id INTEGER PRIMARY KEY, value INTEGER);
            INSERT INTO payload VALUES(1, 0);
            CREATE TRIGGER rollback_batch
            BEFORE UPDATE ON payload
            BEGIN SELECT RAISE(ROLLBACK, 'batch engine rollback'); END;
            """
        )
        with pytest.raises(sqlite3.IntegrityError, match="batch engine rollback"):
            store._atomic_batch(
                "test",
                lambda: store._conn.execute("UPDATE payload SET value=1 WHERE id=1"),
            )
        assert not store._conn.in_transaction
        assert store._conn.execute("SELECT value FROM payload WHERE id=1").fetchone()[0] == 0
    finally:
        store.close()


def test_atomic_batch_still_rolls_back_active_transaction_on_cancellation():
    store = _bare_store()
    try:
        store._conn.execute("CREATE TABLE payload(value INTEGER)")

        def cancelled_write():
            store._conn.execute("INSERT INTO payload VALUES(1)")
            raise KeyboardInterrupt("cancelled")

        with pytest.raises(KeyboardInterrupt, match="cancelled"):
            store._atomic_batch("cancel", cancelled_write)
        assert not store._conn.in_transaction
        assert store._conn.execute("SELECT COUNT(*) FROM payload").fetchone()[0] == 0
    finally:
        store.close()
