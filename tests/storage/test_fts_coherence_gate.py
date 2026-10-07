"""FTS's private content must remain an exact mirror of canonical chunks."""
from __future__ import annotations

import json
import subprocess
import sys

import apsw
import pytest

from codeintel.core.identity import canonical_hash, make_chunk_id, make_file_id
from codeintel.core.models import Chunk, FileRecord
from codeintel.lab.retrieval import index_repository
from codeintel.storage.policy import DerivedStateValidationError, validate_fts_coherence, generation_fts_name
from codeintel.storage.production import ProductionSQLiteStore


DAMAGE = ("missing", "duplicate", "content", "file", "orphan")


def _damage(connection, generation_id, damage):
    statements = {
        "missing": "DELETE FROM chunks_fts WHERE generation_id = ?",
        "duplicate": (
            "INSERT INTO chunks_fts SELECT * FROM chunks_fts WHERE generation_id = ?"
        ),
        "content": "UPDATE chunks_fts SET content = 'unrelated decoy' WHERE generation_id = ?",
        "file": "UPDATE chunks_fts SET file_id = 'wrong_file' WHERE generation_id = ?",
        "orphan": (
            "INSERT INTO chunks_fts (generation_id, chunk_id, file_id, content) "
            "VALUES (?, 'missing_chunk', 'missing_file', 'needle')"
        ),
    }
    connection.execute(statements[damage].replace("chunks_fts", generation_fts_name(generation_id)), (generation_id,))


def _generation(store, sequence):
    generation = store.create_generation(
        "repo", sequence, f"{sequence:064x}",
        metadata={"lifecycle_state": "STAGING"},
    )
    file_id = make_file_id("repo", "sample.py")
    content = "needle first\nneedle second\n"
    store.add_files([
        FileRecord(file_id, "repo", generation.generation_id, "sample.py",
                   canonical_hash(content), len(content.encode("utf-8")))
    ])
    chunks = []
    for line, literal in enumerate(content.splitlines(keepends=True), 1):
        span = (line, 0, line + 1, 0)
        content_hash = canonical_hash(literal)
        chunks.append(Chunk(
            make_chunk_id(file_id, generation.generation_id, span, content_hash),
            file_id, generation.generation_id, "sample.py", span, literal, content_hash,
        ))
    store.index_chunks(chunks[:1])
    return generation, chunks


@pytest.mark.parametrize("damage", DAMAGE)
@pytest.mark.parametrize("command", ["index", "query"])
def test_cli_rejects_fts_drift_without_reusing_or_changing_state(tmp_path, damage, command):
    repo, state = tmp_path / "source", tmp_path / "state"
    repo.mkdir()
    (repo / "sample.py").write_text('def plain():\n    return "zephyr_marker"\n')
    index_repository(repo, state)
    db = state / "db.sqlite"
    connection = apsw.Connection(str(db))
    generation_id = connection.execute("SELECT generation_id FROM generations").fetchone()[0]
    _damage(connection, generation_id, damage)
    # Neither SQLite's page check nor FTS's own content check compares the
    # independently owned chunks table with this content-owning FTS table.
    assert connection.execute("PRAGMA quick_check(1)").fetchone()[0] == "ok"
    table = generation_fts_name(generation_id)
    connection.execute(f'INSERT INTO "{table}"("{table}") VALUES(\'integrity-check\')')
    connection.close()
    before = db.read_bytes()
    args = [sys.executable, "-m", "codeintel.lab.cli", command,
            "--repo", str(repo), "--state-dir", str(state)]
    if command == "query":
        args.append("zephyr_marker")
    result = subprocess.run(args, capture_output=True, timeout=10, check=False)
    assert result.returncode == 2
    assert result.stdout == b""
    error = json.loads(result.stderr)
    assert error["code"] == "DerivedStateValidationError"
    assert "new state directory" in error["recovery"]
    assert b"Traceback" not in result.stderr
    assert str(tmp_path).encode() not in result.stderr
    assert db.read_bytes() == before


@pytest.mark.parametrize("damage", DAMAGE)
def test_activation_rejects_fts_drift_and_rolls_back_deactivation(tmp_path, damage):
    with ProductionSQLiteStore(str(tmp_path / "db.sqlite")) as store:
        old, _ = _generation(store, 1)
        store.update_generation_metadata(old.generation_id, {"lifecycle_state": "COMMITTED"})
        store.activate_generation(old.generation_id)
        new, _ = _generation(store, 2)
        store.update_generation_metadata(new.generation_id, {"lifecycle_state": "COMMITTED"})
        _damage(store._conn, new.generation_id, damage)
        before = store._conn.execute(
            "SELECT generation_id, is_active FROM generations ORDER BY sequence"
        ).fetchall()
        with pytest.raises(DerivedStateValidationError, match="FTS/chunk coherence"):
            store.activate_generation(new.generation_id)
        assert not store._conn.in_transaction
        assert store.get_active_generation("repo").generation_id == old.generation_id
        assert store._conn.execute(
            "SELECT generation_id, is_active FROM generations ORDER BY sequence"
        ).fetchall() == before


def test_counts_cannot_hide_missing_row_balanced_by_duplicate(tmp_path):
    with ProductionSQLiteStore(str(tmp_path / "db.sqlite")) as store:
        generation, chunks = _generation(store, 1)
        store.index_chunks(chunks)
        store._conn.execute(f'DELETE FROM "{generation_fts_name(generation.generation_id)}" WHERE chunk_id = ?', (chunks[0].chunk_id,))
        table = generation_fts_name(generation.generation_id)
        store._conn.execute(f'INSERT INTO "{table}" SELECT * FROM "{table}"')
        with pytest.raises(DerivedStateValidationError, match="FTS/chunk coherence"):
            validate_fts_coherence(store._conn, generation.generation_id)


def test_internal_posting_corruption_is_rejected_by_existing_quick_check(tmp_path):
    repo, state = tmp_path / "source", tmp_path / "state"
    repo.mkdir()
    (repo / "sample.py").write_text('def plain():\n    return "zephyr_marker"\n')
    index_repository(repo, state)
    db = state / "db.sqlite"
    connection = apsw.Connection(str(db))
    generation_id = connection.execute("SELECT generation_id FROM generations").fetchone()[0]
    table = generation_fts_name(generation_id)
    content_rows = list(connection.execute(f'SELECT * FROM "{table}"'))
    connection.execute(
        "CREATE VIRTUAL TABLE decoy USING fts5(chunk_id UNINDEXED, "
        "file_id UNINDEXED, generation_id UNINDEXED, content, tokenize='trigram')"
    )
    connection.execute(
        "INSERT INTO decoy SELECT chunk_id, file_id, generation_id, "
        f"replace(content, 'zephyr', 'quartz') FROM {table}"
    )
    # Substitute structurally valid postings, without changing visible FTS
    # content or canonical chunks. MATCH is silently wrong until checked.
    connection.execute("BEGIN IMMEDIATE")
    connection.execute(f"DELETE FROM {table}_idx")
    connection.execute(f"INSERT INTO {table}_idx SELECT * FROM decoy_idx")
    connection.execute(f"DELETE FROM {table}_data WHERE id > 10")
    connection.execute(f"INSERT INTO {table}_data SELECT * FROM decoy_data WHERE id > 10")
    connection.execute("COMMIT")
    connection.execute("DROP TABLE decoy")
    assert list(connection.execute(f"SELECT * FROM {table}")) == content_rows
    validate_fts_coherence(connection)
    assert list(connection.execute(
        f"SELECT chunk_id FROM {table} WHERE {table} MATCH 'zephyr_marker'"
    )) == []
    assert "checksum mismatch" in connection.execute("PRAGMA quick_check(1)").fetchone()[0]
    connection.close()
    before = db.read_bytes()
    result = subprocess.run(
        [sys.executable, "-m", "codeintel.lab.cli", "query", "--repo", str(repo),
         "--state-dir", str(state), "zephyr_marker"],
        capture_output=True, timeout=10, check=False,
    )
    assert result.returncode == 2 and result.stdout == b""
    assert json.loads(result.stderr)["code"] == "DerivedStateValidationError"
    assert b"Traceback" not in result.stderr
    assert db.read_bytes() == before


def test_clean_staging_and_atomic_replacements_remain_usable(tmp_path):
    path = str(tmp_path / "db.sqlite")
    with ProductionSQLiteStore(path) as writer, ProductionSQLiteStore(path) as reader:
        generation, chunks = _generation(writer, 1)
        validate_fts_coherence(reader._conn)
        # Staged generation data can be incomplete while one writer transaction
        # is replacing it. An independent WAL reader still sees the old mirror.
        writer._conn.execute("BEGIN IMMEDIATE")
        try:
            writer._conn.execute(f'DELETE FROM "{generation_fts_name(generation.generation_id)}"')
            validate_fts_coherence(reader._conn)
        finally:
            writer._conn.execute("ROLLBACK")
        writer.index_chunks(chunks)
        validate_fts_coherence(reader._conn)
        writer.index_chunks(chunks[:1], is_full_replacement=False)
        validate_fts_coherence(reader._conn)
        writer.update_generation_metadata(generation.generation_id, {"lifecycle_state": "COMMITTED"})
        writer.activate_generation(generation.generation_id)
        assert reader.get_active_generation("repo").generation_id == generation.generation_id


@pytest.mark.parametrize("scoped", [False, True])
def test_gate_is_one_read_snapshot_even_during_committed_replacement(tmp_path, scoped):
    path = str(tmp_path / "db.sqlite")
    with ProductionSQLiteStore(path) as writer, ProductionSQLiteStore(path) as reader:
        generation, chunks = _generation(writer, 1)
        calls = []

        class ObservedConnection:
            def execute(self, sql, params=()):
                calls.append(sql)
                return reader._conn.execute(sql, params)
            def __getattr__(self, name):
                return getattr(reader._conn, name)

        steps = 0
        replaced = False

        def concurrent_replacement():
            nonlocal steps, replaced
            steps += 1
            if steps == 30:
                replaced = True
                writer.index_chunks(chunks)
            return False

        reader._conn.set_progress_handler(concurrent_replacement, 1)
        try:
            validate_fts_coherence(
                ObservedConnection(), generation.generation_id if scoped else None
            )
        finally:
            reader._conn.set_progress_handler(None)
        assert replaced
        assert calls[0] == "SAVEPOINT codeintel_fts_read"
        assert calls[-1] == "RELEASE SAVEPOINT codeintel_fts_read"
        assert len([sql for sql in calls if sql.lstrip().startswith("SELECT")]) == (1 if scoped else 2)
        assert not reader._conn.in_transaction
        assert len(reader.get_all_chunk_ids(generation.generation_id)) == 2
        validate_fts_coherence(reader._conn)
