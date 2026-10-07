from pathlib import Path

import apsw
import pytest

import codeintel.storage.production as production
from codeintel.storage.production import ProductionSQLiteStore


def test_existing_database_is_structurally_validated_before_policy_stamping(
    monkeypatch, tmp_path: Path
):
    db = tmp_path / "db.sqlite"
    conn = apsw.Connection(str(db))
    conn.execute("CREATE TABLE sentinel(value TEXT)")
    conn.close()

    calls = []

    def reject_schema(connection):
        calls.append("validate")
        raise RuntimeError("invalid production schema")

    def configure(connection):
        calls.append("configure")

    monkeypatch.setattr(production, "validate_production_schema", reject_schema)
    monkeypatch.setattr(production, "configure_codeintel_connection", configure)

    with pytest.raises(RuntimeError, match="invalid production schema"):
        ProductionSQLiteStore(str(db))

    assert calls == ["validate"]


def test_failed_production_store_close_remains_retryable():
    class FlakyConnection:
        def __init__(self):
            self.calls = 0

        def close(self):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("first close failed")

    store = object.__new__(ProductionSQLiteStore)
    store._closed = False
    store._conn = FlakyConnection()

    with pytest.raises(RuntimeError, match="first close failed"):
        store.close()
    assert store._closed is False

    store.close()
    assert store._closed is True
    assert store._conn.calls == 2
