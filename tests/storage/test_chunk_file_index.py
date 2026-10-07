import sqlite3

from codeintel.storage.sqlite_store import SCHEMA_SQL, SQLiteStore


def test_existing_chunk_database_gets_generation_file_lookup_index(tmp_path):
    db = tmp_path / "existing.sqlite"
    index_sql = (
        "CREATE INDEX IF NOT EXISTS idx_chunks_generation_file "
        "ON chunks(generation_id, file_id);"
    )
    assert index_sql in SCHEMA_SQL
    with sqlite3.connect(db) as connection:
        connection.executescript(SCHEMA_SQL.replace(index_sql, "", 1))

    with SQLiteStore(str(db)):
        pass

    with sqlite3.connect(db) as connection:
        plan = connection.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM chunks WHERE file_id = ? AND generation_id = ?",
            ("file_1", "gen_1"),
        ).fetchall()
    assert any("idx_chunks_generation_file" in row[3] for row in plan)
