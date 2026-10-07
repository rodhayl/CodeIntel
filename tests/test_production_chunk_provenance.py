import pytest

from codeintel.core.identity import canonical_hash, make_chunk_id
from codeintel.core.models import Chunk, Entity, EntityKind, FileRecord
from codeintel.storage.production import ProductionSQLiteStore
from codeintel.storage.policy import generation_fts_name


def _generation(store, sequence=1, marker="a"):
    return store.create_generation(
        repo_id="repo_1",
        sequence=sequence,
        snapshot_hash=marker * 64,
        metadata={"lifecycle_state": "STAGING"},
    )


def _file(store, generation_id, file_id="file_1", rel_path="src/a.py"):
    store.add_files([
        FileRecord(
            file_id=file_id,
            repo_id="repo_1",
            generation_id=generation_id,
            rel_path=rel_path,
            content_hash="file_hash",
            size_bytes=4,
        )
    ])


def _entity(store, generation_id, entity_id="ent_1", file_id="file_1"):
    store.add_entities([
        Entity(
            entity_id=entity_id,
            repo_id="repo_1",
            file_id=file_id,
            generation_id=generation_id,
            name=entity_id,
            qualified_name=f"pkg.{entity_id}",
            kind=EntityKind.FUNCTION,
            span=(1, 0, 1, 4),
        )
    ])


def _chunk(generation_id, *, chunk_id=None, file_id="file_1", rel_path="src/a.py", entity_ids=None):
    span = (1, 0, 1, 4)
    content = "pass"
    c_hash = canonical_hash(content)
    if chunk_id is None:
        chunk_id = make_chunk_id(file_id, generation_id, span, c_hash)
    return Chunk(
        chunk_id=chunk_id,
        file_id=file_id,
        generation_id=generation_id,
        rel_path=rel_path,
        span=span,
        content=content,
        content_hash=c_hash,
        entity_ids=list(entity_ids or []),
    )


def _counts(store, generation_id):
    chunks = store._execute(
        "SELECT COUNT(*) AS n FROM chunks WHERE generation_id = ?",
        (generation_id,),
    ).fetchone()["n"]
    fts = store._execute(
        f'SELECT COUNT(*) AS n FROM "{generation_fts_name(generation_id)}" WHERE generation_id = ?',
        (generation_id,),
    ).fetchone()["n"]
    return chunks, fts


def test_production_chunks_require_one_generation_and_existing_file_path(tmp_path):
    store = ProductionSQLiteStore(str(tmp_path / "db.sqlite"))
    try:
        gen1 = _generation(store, 1, "a")
        gen2 = _generation(store, 2, "b")
        _file(store, gen1.generation_id)
        _file(store, gen2.generation_id)

        with pytest.raises(ValueError, match="exactly one generation"):
            store.index_chunks([
                _chunk(gen1.generation_id, chunk_id="c1"),
                _chunk(gen2.generation_id, chunk_id="c2"),
            ])
        assert _counts(store, gen1.generation_id) == (0, 0)
        assert _counts(store, gen2.generation_id) == (0, 0)

        with pytest.raises(ValueError, match="rel_path does not match file provenance"):
            store.index_chunks([
                _chunk(gen1.generation_id, rel_path="src/forged.py")
            ])
        assert _counts(store, gen1.generation_id) == (0, 0)
    finally:
        store.close()


def test_production_chunks_require_same_generation_entity_ownership(tmp_path):
    store = ProductionSQLiteStore(str(tmp_path / "db.sqlite"))
    try:
        generation = _generation(store)
        _file(store, generation.generation_id, "file_1", "src/a.py")
        _file(store, generation.generation_id, "file_2", "src/b.py")
        _entity(store, generation.generation_id, "ent_a", "file_1")
        _entity(store, generation.generation_id, "ent_b", "file_2")

        with pytest.raises(ValueError, match="entities absent from its generation"):
            store.index_chunks([
                _chunk(generation.generation_id, entity_ids=["ent_missing"])
            ])
        with pytest.raises(ValueError, match="owned by another file"):
            store.index_chunks([
                _chunk(generation.generation_id, entity_ids=["ent_b"])
            ])
        assert _counts(store, generation.generation_id) == (0, 0)

        store.index_chunks([
            _chunk(generation.generation_id, entity_ids=["ent_a"])
        ])
        assert _counts(store, generation.generation_id) == (1, 1)
    finally:
        store.close()


def test_production_chunks_reject_duplicate_ids_and_non_boolean_replacement(tmp_path):
    store = ProductionSQLiteStore(str(tmp_path / "db.sqlite"))
    try:
        generation = _generation(store)
        _file(store, generation.generation_id)
        with pytest.raises(ValueError, match="duplicate chunk_ids"):
            store.index_chunks([
                _chunk(generation.generation_id, chunk_id="same"),
                _chunk(generation.generation_id, chunk_id="same"),
            ])
        with pytest.raises(ValueError, match="is_full_replacement must be boolean"):
            store.index_chunks([
                _chunk(generation.generation_id)
            ], is_full_replacement="false")
        assert _counts(store, generation.generation_id) == (0, 0)
    finally:
        store.close()
