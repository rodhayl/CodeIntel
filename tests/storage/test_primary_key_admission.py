"""Schema admission retains identity constraints before any writable open."""
import json
import subprocess
import sys

import apsw
import pytest

from codeintel.lab.retrieval import index_repository
from codeintel.storage import production
from codeintel.storage.policy import DerivedStateValidationError

KEYS = {'generations': 'generation_id', 'files': 'file_id', 'entities': 'entity_id',
        'relations': 'relation_id', 'chunks': 'chunk_id'}


def _state_bytes(state):
    return {name: (state / name).read_bytes() if (state / name).exists() else b''
            for name in ('db.sqlite', 'db.sqlite-wal', '.codeintel_state_layout')}


def _damage_key(db, table, defect, pending_wal):
    with apsw.Connection(str(db)) as connection:
        connection.pragma('legacy_alter_table', True)
        if pending_wal:
            connection.config(apsw.SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE, True)
        definition = connection.execute('SELECT sql FROM sqlite_schema WHERE name=?', (table,)).fetchone()[0]
        if table == 'generations':
            if defect == 'missing':
                definition = definition.replace('generation_id TEXT PRIMARY KEY', 'generation_id TEXT')
            else:
                definition = definition.replace('generation_id TEXT PRIMARY KEY', 'generation_id TEXT').replace(
                    'repo_id TEXT NOT NULL', 'repo_id TEXT PRIMARY KEY NOT NULL')
        else:
            key = f'PRIMARY KEY (generation_id, {KEYS[table]}),'
            replacement = '' if defect == 'missing' else f'PRIMARY KEY ({KEYS[table]}, generation_id),'
            assert key in definition
            definition = definition.replace(key, replacement)
        connection.execute(f'ALTER TABLE "{table}" RENAME TO old_table')
        connection.execute(definition)
        connection.execute(f'INSERT INTO "{table}" SELECT * FROM old_table')
        connection.execute('DROP TABLE old_table')


@pytest.mark.parametrize('table', KEYS)
@pytest.mark.parametrize('defect', ['missing', 'wrong-order'])
@pytest.mark.parametrize('pending_wal', [False, True])
def test_primary_key_required_before_writable_open(tmp_path, monkeypatch, table, defect, pending_wal):
    db = tmp_path / 'db.sqlite'
    with production.ProductionSQLiteStore(str(db)):
        pass
    _damage_key(db, table, defect, pending_wal)
    before = _state_bytes(tmp_path)
    assert bool(before['db.sqlite-wal']) == pending_wal
    connection_type = apsw.Connection
    flags = []
    def connect(*args, **kwargs):
        flags.append(kwargs.get('flags', 0))
        return connection_type(*args, **kwargs)
    monkeypatch.setattr(production.apsw, 'Connection', connect)
    with pytest.raises(DerivedStateValidationError, match=f"{table!r}.*primary key"):
        production.ProductionSQLiteStore(str(db))
    assert flags == [apsw.SQLITE_OPEN_READONLY]
    assert _state_bytes(tmp_path) == before


@pytest.mark.parametrize('command', ['index', 'query'])
def test_missing_entity_primary_key_has_nonmutating_cli_recovery(tmp_path, command):
    repo, state = tmp_path / 'repo', tmp_path / 'state'
    repo.mkdir()
    (repo / 'source.py').write_text('def target(): return 1\n')
    index_repository(repo, state)
    _damage_key(state / 'db.sqlite', 'entities', 'missing', False)
    before = _state_bytes(state)
    argv = [sys.executable, '-m', 'codeintel.lab.cli', command,
            '--repo', str(repo), '--state-dir', str(state)]
    if command == 'query':
        argv.append('target')
    result = subprocess.run(argv, capture_output=True, timeout=5)
    assert result.returncode == 2 and result.stdout == b''
    assert json.loads(result.stderr)['code'] == 'DerivedStateValidationError'
    assert b'Traceback' not in result.stderr and str(tmp_path).encode() not in result.stderr
    assert _state_bytes(state) == before
