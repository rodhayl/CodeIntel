import os

import apsw
import pytest

from codeintel.core.models import FileRecord
from codeintel.storage.lifecycle import cleanup_orphan_vector_artifacts
from codeintel.storage import policy
from codeintel.storage.production import ProductionSQLiteStore


def _state(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    (state / ".codeintel_state_layout").write_text("codeintel-state-v1\n", encoding="utf-8")
    return state


def _make_store(tmp_path):
    state = _state(tmp_path)
    store = ProductionSQLiteStore(str(state / "db.sqlite"))
    return state, store


def test_production_store_refuses_symlinked_db_file(tmp_path):
    state = _state(tmp_path)
    outside = tmp_path / "outside.db.sqlite"
    outside.write_bytes(b"")
    (state / "db.sqlite").symlink_to(outside)
    with pytest.raises(RuntimeError, match="must not be a symlink"):
        ProductionSQLiteStore(str(state / "db.sqlite"))


def test_schema_identity_rejects_regular_table_impersonating_chunks_fts(tmp_path):
    state, store = _make_store(tmp_path)
    generation = store.create_generation("repo", 1, "a" * 64)
    table = policy.generation_fts_name(generation.generation_id)
    store.close()
    conn = apsw.Connection(str(state / "db.sqlite"))
    conn.execute(f'DROP TABLE "{table}"')
    conn.execute(
        f'CREATE TABLE "{table}" (chunk_id TEXT, file_id TEXT, generation_id TEXT, content TEXT)'
    )
    conn.close()

    with pytest.raises(RuntimeError, match="canonical FTS5 virtual table"):
        ProductionSQLiteStore(str(state / "db.sqlite"))


def test_schema_identity_rejects_missing_generation_foreign_key(tmp_path):
    state, store = _make_store(tmp_path)
    store.close()
    conn = apsw.Connection(str(state / "db.sqlite"))
    conn.pragma("foreign_keys", False)
    conn.execute("ALTER TABLE files RENAME TO files_old")
    conn.execute(
        "CREATE TABLE files ("
        "file_id TEXT NOT NULL, repo_id TEXT NOT NULL, generation_id TEXT NOT NULL, "
        "rel_path TEXT NOT NULL, content_hash TEXT NOT NULL, size_bytes INTEGER NOT NULL, "
        "PRIMARY KEY (generation_id, file_id))"
    )
    conn.execute(
        "INSERT INTO files SELECT file_id, repo_id, generation_id, rel_path, content_hash, size_bytes FROM files_old"
    )
    conn.execute("DROP TABLE files_old")
    conn.close()

    with pytest.raises(RuntimeError, match="canonical generation foreign key"):
        ProductionSQLiteStore(str(state / "db.sqlite"))


def test_open_rejects_foreign_key_corruption_even_when_schema_shape_is_valid(tmp_path):
    state, store = _make_store(tmp_path)
    store.close()
    conn = apsw.Connection(str(state / "db.sqlite"))
    conn.pragma("foreign_keys", False)
    conn.execute(
        "INSERT INTO files (file_id, repo_id, generation_id, rel_path, content_hash, size_bytes) "
        "VALUES ('f_orphan', 'repo', 'missing_generation', 'src/x.py', 'h', 1)"
    )
    conn.close()

    with pytest.raises(RuntimeError, match="foreign-key integrity"):
        ProductionSQLiteStore(str(state / "db.sqlite"))


def test_production_multirow_file_write_rolls_back_as_one_batch(tmp_path):
    _state_dir, store = _make_store(tmp_path)
    gen = store.create_generation("repo", 1, "abcdef0123456789")
    good = FileRecord("f_good", "repo", gen.generation_id, "src/good.py", "h1", 1)
    bad = FileRecord("f_bad", "repo", gen.generation_id, "src/bad.py", "h2", None)  # type: ignore[arg-type]

    with pytest.raises(Exception):
        store.add_files([good, bad])
    assert store.get_files(gen.generation_id) == []
    store.close()


def test_cleanup_refuses_sidecar_directory_symlink_without_touching_target(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    orphan = outside / "vectors_orphan.npz"
    orphan.write_bytes(b"do-not-delete")
    (state / ".codeintel_vectors").symlink_to(outside, target_is_directory=True)

    report = cleanup_orphan_vector_artifacts(str(state), {"gen_valid"})
    assert report["unsafe_sidecar_dir"] is True
    assert orphan.read_bytes() == b"do-not-delete"
    assert report["orphan_sidecars_deleted"] == []


def test_connection_policy_is_canonical_after_open(tmp_path):
    _state_dir, store = _make_store(tmp_path)
    assert int(store._conn.pragma("foreign_keys")) == 1
    assert str(store._conn.pragma("journal_mode")).lower() == "wal"
    assert int(store._conn.pragma("synchronous")) == 1
    assert str(store._conn.execute("PRAGMA quick_check(1)").fetchone()["quick_check"]).lower() == "ok"
    store.close()


def test_existing_database_runs_integrity_check_once_and_keeps_policy(tmp_path, monkeypatch):
    state, store = _make_store(tmp_path)
    store.close()

    calls = 0
    original = policy._assert_database_integrity

    def checked(connection):
        nonlocal calls
        calls += 1
        return original(connection)

    monkeypatch.setattr(policy, "_assert_database_integrity", checked)
    with ProductionSQLiteStore(str(state / "db.sqlite")) as reopened:
        assert int(reopened._conn.pragma("foreign_keys")) == 1
        assert str(reopened._conn.pragma("journal_mode")).lower() == "wal"
    assert calls == 1


def test_prevalidated_connection_applies_missing_canonical_index(tmp_path):
    state, store = _make_store(tmp_path)
    store.close()
    db = state / "db.sqlite"
    connection = apsw.Connection(str(db))
    connection.execute("DROP INDEX idx_chunks_generation_file")
    connection.close()

    with ProductionSQLiteStore(str(db)) as reopened:
        row = reopened._conn.execute(
            "SELECT name FROM sqlite_schema WHERE name = 'idx_chunks_generation_file'"
        ).fetchone()
        assert row is not None
