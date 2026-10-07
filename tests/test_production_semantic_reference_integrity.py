import pytest

from codeintel.core.models import (
    Entity,
    EntityKind,
    FileRecord,
    Relation,
    RelationType,
    TrustClass,
)
from codeintel.storage.production import ProductionSQLiteStore


def _generation(store):
    return store.create_generation(
        repo_id="repo_1",
        sequence=1,
        snapshot_hash="a" * 64,
        metadata={"lifecycle_state": "STAGING"},
    )


def _entity(entity_id, generation_id, file_id="file_1"):
    return Entity(
        entity_id=entity_id,
        repo_id="repo_1",
        file_id=file_id,
        generation_id=generation_id,
        name=entity_id,
        qualified_name=f"pkg.{entity_id}",
        kind=EntityKind.FUNCTION,
        span=(1, 0, 1, 4),
    )


def _relation(generation_id, source_id="ent_a", target_id="ent_b", file_id="file_1"):
    return Relation(
        relation_id="rel_1",
        repo_id="repo_1",
        generation_id=generation_id,
        source_id=source_id,
        target_id=target_id,
        rel_type=RelationType.CALLS,
        file_id=file_id,
        span=(1, 0, 1, 4),
        trust_class=TrustClass.RESOLVED,
    )


def test_production_entities_require_file_in_same_generation(tmp_path):
    store = ProductionSQLiteStore(str(tmp_path / "db.sqlite"))
    try:
        generation = _generation(store)
        with pytest.raises(ValueError, match="files absent from its generation"):
            store.add_entities([_entity("ent_a", generation.generation_id)])
        count = store._execute(
            "SELECT COUNT(*) AS n FROM entities WHERE generation_id = ?",
            (generation.generation_id,),
        ).fetchone()["n"]
        assert count == 0

        store.add_files([
            FileRecord(
                file_id="file_1",
                repo_id="repo_1",
                generation_id=generation.generation_id,
                rel_path="a.py",
                content_hash="h1",
                size_bytes=4,
            )
        ])
        store.add_entities([_entity("ent_a", generation.generation_id)])
        assert store.get_entity("ent_a", generation_id=generation.generation_id) is not None
    finally:
        store.close()


def test_production_relations_require_file_and_both_endpoints_in_generation(tmp_path):
    store = ProductionSQLiteStore(str(tmp_path / "db.sqlite"))
    try:
        generation = _generation(store)
        store.add_files([
            FileRecord(
                file_id="file_1",
                repo_id="repo_1",
                generation_id=generation.generation_id,
                rel_path="a.py",
                content_hash="h1",
                size_bytes=4,
            )
        ])
        store.add_entities([
            _entity("ent_a", generation.generation_id),
            _entity("ent_b", generation.generation_id),
        ])

        with pytest.raises(ValueError, match="entities absent from its generation"):
            store.add_relations([
                _relation(generation.generation_id, target_id="ent_missing")
            ])
        with pytest.raises(ValueError, match="files absent from its generation"):
            store.add_relations([
                _relation(generation.generation_id, file_id="file_missing")
            ])

        count = store._execute(
            "SELECT COUNT(*) AS n FROM relations WHERE generation_id = ?",
            (generation.generation_id,),
        ).fetchone()["n"]
        assert count == 0

        store.add_relations([_relation(generation.generation_id)])
        count = store._execute(
            "SELECT COUNT(*) AS n FROM relations WHERE generation_id = ?",
            (generation.generation_id,),
        ).fetchone()["n"]
        assert count == 1
    finally:
        store.close()
