from __future__ import annotations

import asyncio
import sqlite3
import pytest

import codeintel.storage.policy as policy


class Connection:
    def __init__(self, exc_type, *, engine_rollback=False):
        self.db = sqlite3.connect(':memory:', isolation_level=None)
        self.db.execute('CREATE TABLE generations(repo_id TEXT, is_active INTEGER)')
        self.exc_type = exc_type
        self.engine_rollback = engine_rollback
        self.app_id = 0
        self.user_version = 0
        self.foreign_keys = 1

    @property
    def in_transaction(self):
        return self.db.in_transaction

    def set_busy_timeout(self, value):
        pass

    def pragma(self, name, value=None):
        if name == 'application_id':
            if value is not None:
                self.app_id = value
            return self.app_id
        if name == 'user_version':
            if value is not None:
                self.user_version = value
            return self.user_version
        if name == 'foreign_keys':
            if value is not None:
                self.foreign_keys = int(bool(value))
            return self.foreign_keys
        if name == 'journal_mode':
            return 'wal'
        if name == 'synchronous':
            return 1
        return 0

    def execute(self, sql, params=()):
        if sql.startswith('CREATE UNIQUE INDEX'):
            if self.engine_rollback and self.db.in_transaction:
                self.db.execute('ROLLBACK')
            raise self.exc_type('original policy failure')
        return self.db.execute(sql, params)


@pytest.fixture(autouse=True)
def bypass_unrelated_policy_probes(monkeypatch):
    monkeypatch.setattr(policy, 'is_codeintel_db', lambda connection: True)
    monkeypatch.setattr(policy, 'validate_legacy_schema', lambda connection: None)
    monkeypatch.setattr(policy, 'validate_production_schema', lambda connection: None)
    monkeypatch.setattr(policy, '_assert_single_active_generation', lambda connection: None)
    monkeypatch.setattr(policy, '_assert_database_integrity', lambda connection: None)


@pytest.mark.parametrize('exc_type', [KeyboardInterrupt, asyncio.CancelledError])
def test_policy_cancellation_rolls_back_open_savepoint(exc_type):
    connection = Connection(exc_type)
    try:
        with pytest.raises(exc_type, match='original policy failure'):
            policy.configure_codeintel_connection(connection)
        assert not connection.in_transaction
    finally:
        connection.db.close()


def test_engine_rollback_does_not_mask_original_policy_error():
    connection = Connection(RuntimeError, engine_rollback=True)
    try:
        with pytest.raises(RuntimeError, match='original policy failure'):
            policy.configure_codeintel_connection(connection)
        assert not connection.in_transaction
    finally:
        connection.db.close()
