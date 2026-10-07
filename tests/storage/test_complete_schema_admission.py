"""Every stored column consumed by the runtime must pass the admission gate."""
from __future__ import annotations

import json
import subprocess
import sys

import apsw
import pytest

from codeintel.lab.retrieval import index_repository
from codeintel.storage import production
from codeintel.storage.policy import DerivedStateValidationError


PREVIOUSLY_UNCHECKED_COLUMNS = [
    ("generations", "created_at"),
    ("generations", "build_fingerprint"),
    *[("entities", column) for column in (
        "start_line", "start_col", "end_line", "end_col", "docstring",
        "signature", "properties_json",
    )],
    *[("relations", column) for column in (
        "file_id", "start_line", "start_col", "end_line", "end_col", "properties_json",
    )],
    *[("chunks", column) for column in (
        "start_line", "start_col", "end_line", "end_col", "entity_ids_json",
    )],
]


def _state_bytes(state):
    # SHM is transient reader coordination; database and WAL are persisted state.
    # A read-only WAL connection may create an empty WAL sidecar. Its absence
    # and an empty file both represent no persisted frames.
    return {name: (state / name).read_bytes() if (state / name).exists() else b""
            for name in ("db.sqlite", "db.sqlite-wal", ".codeintel_state_layout")}


def _drop_column(db, table, column, pending_wal):
    connection = apsw.Connection(str(db))
    try:
        if pending_wal:
            connection.config(apsw.SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE, True)
        connection.execute(f'ALTER TABLE "{table}" DROP COLUMN "{column}"')
    finally:
        connection.close()


@pytest.mark.parametrize("table,column", PREVIOUSLY_UNCHECKED_COLUMNS)
@pytest.mark.parametrize("pending_wal", [False, True])
def test_missing_runtime_column_rejected_before_writable_open(tmp_path, monkeypatch, table, column, pending_wal):
    db = tmp_path / "db.sqlite"
    with production.ProductionSQLiteStore(str(db)):
        pass
    _drop_column(db, table, column, pending_wal)
    before = _state_bytes(tmp_path)
    assert bool(before["db.sqlite-wal"]) == pending_wal
    connection_type = apsw.Connection
    opened_flags = []

    def connect(*args, **kwargs):
        opened_flags.append(kwargs.get("flags", 0))
        return connection_type(*args, **kwargs)

    monkeypatch.setattr(production.apsw, "Connection", connect)
    with pytest.raises(DerivedStateValidationError, match=f"{table!r}.*{column}"):
        with production.ProductionSQLiteStore(str(db)):
            pass
    assert opened_flags == [apsw.SQLITE_OPEN_READONLY]
    assert _state_bytes(tmp_path) == before


@pytest.mark.parametrize("table,column", [
    ("generations", "build_fingerprint"),
    ("entities", "start_line"),
    ("chunks", "entity_ids_json"),
])
@pytest.mark.parametrize("command", ["index", "query"])
@pytest.mark.parametrize("pending_wal", [False, True])
def test_missing_runtime_column_has_nonmutating_cli_recovery(tmp_path, table, column, command, pending_wal):
    repo, state = tmp_path / "repo", tmp_path / "state"
    repo.mkdir()
    (repo / "a.py").write_text("def target(): return 1\n")
    index_repository(repo, state)
    _drop_column(state / "db.sqlite", table, column, pending_wal)
    before = _state_bytes(state)
    assert bool(before["db.sqlite-wal"]) == pending_wal
    args = [sys.executable, "-m", "codeintel.lab.cli", command,
            "--repo", str(repo), "--state-dir", str(state)]
    if command == "query":
        args.append("target")
    result = subprocess.run(args, capture_output=True, timeout=10, check=False)
    assert result.returncode == 2
    assert result.stdout == b""
    error = json.loads(result.stderr)
    assert error["status"] == "ERROR"
    assert error["code"] == "DerivedStateValidationError"
    assert "new state directory" in error["recovery"]
    assert b"Traceback" not in result.stderr
    assert str(tmp_path).encode() not in result.stderr
    assert _state_bytes(state) == before
