from __future__ import annotations

import asyncio

import apsw
import pytest

import codeintel.storage.production as production_module
from codeintel.storage.production import ProductionSQLiteStore


class _FakeConnection:
    def __init__(self, *, fail_close: bool = False):
        self.closed = False
        self.fail_close = fail_close

    def close(self):
        self.closed = True
        if self.fail_close:
            raise RuntimeError("close failed")


@pytest.mark.parametrize("exc_type", [KeyboardInterrupt, asyncio.CancelledError])
def test_init_cancellation_closes_connection(tmp_path, monkeypatch, exc_type):
    connection = _FakeConnection()

    def fake_base_init(self, db_path):
        self._conn = connection

    def cancel_validation(conn):
        raise exc_type("cancel production init")

    monkeypatch.setattr(production_module.BaseSQLiteStore, "__init__", fake_base_init)
    monkeypatch.setattr(production_module, "validate_production_schema", cancel_validation)

    with pytest.raises(exc_type, match="cancel production init"):
        ProductionSQLiteStore(str(tmp_path / "db.sqlite"))

    assert connection.closed is True


def test_init_cleanup_failure_does_not_replace_primary_error(tmp_path, monkeypatch):
    connection = _FakeConnection(fail_close=True)

    def fake_base_init(self, db_path):
        self._conn = connection

    def cancel_validation(conn):
        raise KeyboardInterrupt("cancel production init")

    monkeypatch.setattr(production_module.BaseSQLiteStore, "__init__", fake_base_init)
    monkeypatch.setattr(production_module, "validate_production_schema", cancel_validation)

    with pytest.raises(KeyboardInterrupt, match="cancel production init") as captured:
        ProductionSQLiteStore(str(tmp_path / "db.sqlite"))

    assert connection.closed is True
    assert any("close failed" in note for note in captured.value.__notes__)


def test_existing_database_closes_prevalidated_connection_if_initialization_fails(
    tmp_path, monkeypatch
):
    db = tmp_path / "db.sqlite"
    with ProductionSQLiteStore(str(db)):
        pass

    opened = []

    def cancel_initialization(self):
        opened.append(self._conn)
        raise KeyboardInterrupt("cancel after preflight")

    monkeypatch.setattr(production_module.BaseSQLiteStore, "_init_db", cancel_initialization)
    with pytest.raises(KeyboardInterrupt, match="cancel after preflight"):
        ProductionSQLiteStore(str(db))

    assert len(opened) == 1
    with pytest.raises(apsw.ConnectionClosedError):
        opened[0].pragma("foreign_keys")
