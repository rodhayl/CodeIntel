from dataclasses import replace

import pytest

from codeintel.core.identity import canonical_hash, make_chunk_id, make_file_id
from codeintel.core.models import Chunk, FileRecord
from codeintel.storage.production import ProductionSQLiteStore


REPO_ID = "repo_test"
REL_PATH = "src/example.py"


def _prepared_store(tmp_path):
    store = ProductionSQLiteStore(str(tmp_path / "db.sqlite"))
    generation = store.create_generation(
        repo_id=REPO_ID,
        sequence=1,
        snapshot_hash="0" * 64,
        build_fingerprint="build",
        metadata={"lifecycle_state": "STAGING"},
    )
    file_id = make_file_id(REPO_ID, REL_PATH)
    store.add_files(
        [
            FileRecord(
                file_id=file_id,
                repo_id=REPO_ID,
                generation_id=generation.generation_id,
                rel_path=REL_PATH,
                content_hash=canonical_hash("α = 1\n"),
                size_bytes=len("α = 1\n".encode("utf-8")),
            )
        ]
    )
    return store, generation.generation_id, file_id


def _canonical_chunk(generation_id: str, file_id: str) -> Chunk:
    content = "α = 1\n"
    span = (1, 0, 2, 0)
    content_hash = canonical_hash(content)
    return Chunk(
        chunk_id=make_chunk_id(file_id, generation_id, span, content_hash),
        file_id=file_id,
        generation_id=generation_id,
        rel_path=REL_PATH,
        span=span,
        content=content,
        content_hash=content_hash,
        entity_ids=[],
    )


def _stored_chunk_count(store: ProductionSQLiteStore) -> int:
    return int(store._execute("SELECT COUNT(*) AS count FROM chunks").fetchone()["count"])


def test_production_store_accepts_canonical_literal_chunk_identity(tmp_path):
    store, generation_id, file_id = _prepared_store(tmp_path)
    try:
        chunk = _canonical_chunk(generation_id, file_id)
        store.index_chunks([chunk])
        assert _stored_chunk_count(store) == 1
        row = store._execute(
            "SELECT chunk_id, content_hash, start_line, start_col, end_line, end_col "
            "FROM chunks WHERE generation_id = ?",
            (generation_id,),
        ).fetchone()
        assert row["chunk_id"] == chunk.chunk_id
        assert row["content_hash"] == chunk.content_hash
        assert (row["start_line"], row["start_col"], row["end_line"], row["end_col"]) == chunk.span
    finally:
        store.close()


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda chunk: replace(chunk, content_hash="0" * 64), "content_hash"),
        (lambda chunk: replace(chunk, chunk_id="chk_forged"), "chunk_id"),
        (lambda chunk: replace(chunk, span=(1, 0, 1, 0)), "span end"),
        (lambda chunk: replace(chunk, span=(1, True, 2, 0)), "coordinates must be integers"),
    ],
)
def test_production_store_rejects_forged_chunk_provenance_before_write(
    tmp_path, mutation, message
):
    store, generation_id, file_id = _prepared_store(tmp_path)
    try:
        forged = mutation(_canonical_chunk(generation_id, file_id))
        with pytest.raises(ValueError, match=message):
            store.index_chunks([forged])
        assert _stored_chunk_count(store) == 0
    finally:
        store.close()


def test_production_store_rejects_duplicate_chunk_ids_before_replacement(tmp_path):
    store, generation_id, file_id = _prepared_store(tmp_path)
    try:
        chunk = _canonical_chunk(generation_id, file_id)
        with pytest.raises(ValueError, match="duplicate chunk_ids"):
            store.index_chunks([chunk, chunk])
        assert _stored_chunk_count(store) == 0
    finally:
        store.close()
