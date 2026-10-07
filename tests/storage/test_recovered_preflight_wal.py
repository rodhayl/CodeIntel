"""Incompatible state remains recoverable, including committed pending WAL."""
from pathlib import Path
import hashlib

import apsw
import pytest

from codeintel.storage import production
from codeintel.storage.policy import DerivedStateValidationError


def snapshot(db):
    return {suffix: hashlib.sha256(Path(str(db) + suffix).read_bytes()).hexdigest()
            for suffix in ('', '-wal') if Path(str(db) + suffix).exists()}


def mark_v1_with_pending_wal(connection):
    connection.config(apsw.SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE, True)
    connection.pragma('user_version', 1)
    connection.execute('CREATE TABLE recovery_sentinel(value TEXT)')
    connection.execute("INSERT INTO recovery_sentinel VALUES('retained')")


def assert_effective_v1(db, connection_type=apsw.Connection):
    reader = connection_type(str(db), flags=apsw.SQLITE_OPEN_READONLY)
    try:
        assert reader.pragma('user_version') == 1
        assert list(reader.execute('SELECT value FROM recovery_sentinel')) == [('retained',)]
    finally:
        reader.close()


@pytest.mark.parametrize('writer_live', [False, True])
def test_v1_rejection_preserves_database_and_pending_wal(tmp_path, writer_live):
    db = tmp_path / 'db.sqlite'
    with production.ProductionSQLiteStore(str(db)):
        pass
    writer = apsw.Connection(str(db))
    mark_v1_with_pending_wal(writer)
    if not writer_live:
        writer.close()
    try:
        before = snapshot(db)
        assert '-wal' in before
        with pytest.raises(DerivedStateValidationError, match='user_version 1'):
            production.ProductionSQLiteStore(str(db))
        # SHM reader-coordination bytes are deliberately not part of this promise.
        assert snapshot(db) == before
        assert_effective_v1(db)
        assert snapshot(db) == before
    finally:
        if writer_live:
            writer.close()


def test_version_race_between_readonly_and_readwrite_probes_preserves_wal(tmp_path, monkeypatch):
    db = tmp_path / 'db.sqlite'
    with production.ProductionSQLiteStore(str(db)):
        pass
    connection_type = apsw.Connection
    state = {'readonly_probes': 0, 'readwrite_probes': 0}

    class RaceAfterReadonlyClose:
        def __init__(self, connection):
            self.connection = connection

        def __getattr__(self, name):
            return getattr(self.connection, name)

        def close(self):
            self.connection.close()
            writer = connection_type(str(db))
            mark_v1_with_pending_wal(writer)
            writer.close()
            state['before'] = snapshot(db)

    def connect(*args, **kwargs):
        connection = connection_type(*args, **kwargs)
        if kwargs.get('flags', 0) & apsw.SQLITE_OPEN_READONLY:
            state['readonly_probes'] += 1
            return RaceAfterReadonlyClose(connection)
        state['readwrite_probes'] += 1
        return connection

    monkeypatch.setattr(production.apsw, 'Connection', connect)
    with pytest.raises(DerivedStateValidationError, match='user_version 1'):
        store = production.ProductionSQLiteStore(str(db))
        store.close()
    assert state['readonly_probes'] == state['readwrite_probes'] == 1
    assert snapshot(db) == state['before']
    assert_effective_v1(db, connection_type)
    assert snapshot(db) == state['before']
