"""Name lookup intersects caller repository and generation scope."""
from __future__ import annotations

import sqlite3
import threading

import pytest

from codeintel.core.models import Entity, EntityKind, FileRecord
from codeintel.storage.production import ProductionSQLiteStore
from codeintel.storage.sqlite_store import SCHEMA_SQL


@pytest.fixture
def scoped_store():
    store = object.__new__(ProductionSQLiteStore)
    store._lock = threading.RLock()
    store._closed = False
    store._conn = sqlite3.connect(':memory:', isolation_level=None)
    store._conn.row_factory = lambda cur, row: {c[0]: row[i] for i, c in enumerate(cur.description)}
    store._conn.executescript(SCHEMA_SQL)
    generations = {}
    for repo, sequence, digest, suffix, active in [
        ('repo_a', 1, 'a', 'a', True), ('repo_b', 1, 'b', 'b', True),
        ('repo_a', 2, 'c', 'staging', False),
    ]:
        gen = store.create_generation(repo, sequence, digest * 64, metadata={'lifecycle_state': 'COMMITTED'})
        generations[suffix] = gen.generation_id
        store.add_files([FileRecord('file_' + suffix, repo, gen.generation_id, 'module.py', digest * 64, 1)])
        store.add_entities([Entity('ent_' + suffix, repo, 'file_' + suffix, gen.generation_id,
                                   'shared', 'module.shared', EntityKind.FUNCTION, (1, 0, 1, 1))])
        if active:
            store.activate_generation(gen.generation_id)
    try:
        yield store, generations
    finally:
        store._conn.close()


@pytest.mark.parametrize('name', ['shared', 'module.shared'])
@pytest.mark.parametrize('repo,expected', [('repo_a', ['ent_a']), ('repo_b', ['ent_b']), ('missing', []), ('', [])])
def test_repository_scope_is_honored(scoped_store, name, repo, expected):
    store, _ = scoped_store
    assert [e.entity_id for e in store.get_entities_by_name(name, repo_id=repo)] == expected


def test_explicit_generation_cannot_override_repository_scope(scoped_store):
    store, generations = scoped_store
    assert store.get_entities_by_name('shared', repo_id='repo_a', generation_id=generations['b']) == []


def test_explicit_empty_generation_cannot_widen_to_active_repositories(scoped_store):
    store, _ = scoped_store
    assert store.get_entities_by_name('shared', generation_id='') == []


def test_repository_scope_checks_generation_owner_not_only_entity_column(scoped_store):
    store, generations = scoped_store
    store._conn.execute('UPDATE entities SET repo_id = ? WHERE generation_id = ?', ('repo_a', generations['b']))
    assert [e.entity_id for e in store.get_entities_by_name('shared', repo_id='repo_a')] == ['ent_a']


def test_unscoped_and_explicit_staging_controls(scoped_store):
    store, generations = scoped_store
    assert sorted(e.entity_id for e in store.get_entities_by_name('shared')) == ['ent_a', 'ent_b']
    assert [e.entity_id for e in store.get_entities_by_name('shared', repo_id='repo_a', generation_id=generations['staging'])] == ['ent_staging']
    assert store.get_entities_by_name('absent', repo_id='repo_a') == []
