"""Malformed persisted rows are storage recovery, not Python tracebacks."""
from __future__ import annotations

import json
import subprocess
import sys

import apsw
import pytest

from codeintel.lab.retrieval import index_repository, query_repository, verify_packet
from codeintel.service import create_default_service
from codeintel.storage.policy import DerivedStateValidationError
from codeintel.storage.production import ProductionSQLiteStore


CORRUPTIONS = [
    ("entities", "kind", "BROKEN"),
    ("entities", "properties_json", "[1]"),
    ("entities", "properties_json", "{"),
    ("entities", "start_line", "BROKEN"),
    ("entities", "start_col", -1),
    ("chunks", "entity_ids_json", "{}"),
    ("chunks", "entity_ids_json", "{"),
    ("chunks", "entity_ids_json", '["entity", 4]'),
    ("chunks", "entity_ids_json", b"[]"),
    ("chunks", "entity_ids_json", r'["\ud800"]'),
    ("chunks", "start_line", "BROKEN"),
    ("chunks", "end_line", 0),
    ("generations", "sequence", "BROKEN"),
    ("generations", "is_active", 2),
    ("files", "size_bytes", "BROKEN"),
]


def _fixture(tmp_path):
    repo, state = tmp_path / "repo", tmp_path / "state"
    repo.mkdir()
    (repo / "sample.py").write_text('def target():\n    return "zephyr_marker"\n')
    index_repository(repo, state)
    return repo, state


def _persisted_bytes(state):
    return {name: (state / name).read_bytes() if (state / name).exists() else b""
            for name in ("db.sqlite", "db.sqlite-wal", ".codeintel_state_layout")}


@pytest.mark.parametrize("command", ["index", "query"])
@pytest.mark.parametrize("pending_wal", [False, True])
@pytest.mark.parametrize("table,column,value", CORRUPTIONS)
def test_cli_rejects_malformed_rows_before_mutation(tmp_path, command, pending_wal, table, column, value):
    repo, state = _fixture(tmp_path)
    connection = apsw.Connection(str(state / "db.sqlite"))
    if pending_wal:
        connection.config(apsw.SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE, True)
    connection.execute(f"UPDATE {table} SET {column} = ?", (value,))
    connection.close()
    before = _persisted_bytes(state)
    args = [sys.executable, "-m", "codeintel.lab.cli", command,
            "--repo", str(repo), "--state-dir", str(state)]
    if command == "query":
        args.append("target")
    result = subprocess.run(args, capture_output=True, timeout=10, check=False)
    assert result.returncode == 2 and result.stdout == b""
    error = json.loads(result.stderr)
    assert error["code"] == "DerivedStateValidationError"
    assert "new state directory" in error["recovery"]
    assert b"Traceback" not in result.stderr
    assert str(tmp_path).encode() not in result.stderr
    assert _persisted_bytes(state) == before


@pytest.mark.parametrize("value", ["{}", "{", '[4]', r'["\ud800"]'])
def test_live_chunk_decoder_uses_storage_error(tmp_path, value):
    repo, state = _fixture(tmp_path)
    with create_default_service(str(repo), str(state)) as service:
        service.graph_store._conn.execute("UPDATE chunks SET entity_ids_json = ?", (value,))
        with pytest.raises(DerivedStateValidationError, match="entity_ids_json"):
            service.search("zephyr_marker")


def test_live_entity_decoder_uses_storage_error(tmp_path):
    repo, state = _fixture(tmp_path)
    with create_default_service(str(repo), str(state)) as service:
        service.graph_store._conn.execute("UPDATE entities SET kind = 'BROKEN'")
        with pytest.raises(DerivedStateValidationError, match="entity kind"):
            service.search("target")


@pytest.mark.parametrize("empty", [None, ""])
def test_valid_rows_and_legacy_empty_json_remain_usable(tmp_path, empty):
    repo, state = _fixture(tmp_path)
    with ProductionSQLiteStore(str(state / "db.sqlite")) as store:
        store._conn.execute("UPDATE entities SET properties_json = ?", (empty,))
        store._conn.execute("UPDATE chunks SET entity_ids_json = ?", (empty,))
    packet, _ = query_repository(repo, state, "target")
    assert packet["status"] == "OK"
    verify_packet(repo, packet)
    assert index_repository(repo, state)[0]["status"] == "REUSED_EXISTING"


def test_decoder_does_not_reclassify_unexpected_programming_error(monkeypatch):
    from codeintel.storage.sqlite_store import SQLiteStore

    def unexpected(_row):
        raise RuntimeError("programmer defect")

    monkeypatch.setattr(SQLiteStore, "_row_to_chunk", staticmethod(unexpected))
    row = dict(start_line=1, start_col=0, end_line=1, end_col=1, entity_ids_json="[]")
    with pytest.raises(RuntimeError, match="programmer defect") as caught:
        ProductionSQLiteStore._row_to_chunk(row)
    assert type(caught.value) is RuntimeError


def test_escaped_supplementary_json_text_is_not_rejected():
    from codeintel.storage.policy import decode_stored_json

    assert decode_stored_json(r'["\ud83d\ude00"]', label="entity_ids_json", shape=list) == ["😀"]
