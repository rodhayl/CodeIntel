from __future__ import annotations

import pytest

from codeintel.core.models import Entity, EntityKind
from codeintel.parsing.semantic_merger import SemanticMerger


def _entity(entity_id: str, qualified_name: str, *, span=(1, 0, 1, 1)) -> Entity:
    return Entity(
        entity_id=entity_id,
        repo_id="repo_1",
        file_id="file_1",
        generation_id="gen_1",
        name=qualified_name.rsplit(".", 1)[-1],
        qualified_name=qualified_name,
        kind=EntityKind.FUNCTION,
        span=span,
    )


def test_semantic_universe_rejects_conflicting_duplicate_entity_id():
    parsed = _entity("ent_same", "pkg.new")
    existing = _entity("ent_same", "pkg.old", span=(2, 0, 2, 1))
    with pytest.raises(ValueError, match="conflicting duplicate entity_id"):
        SemanticMerger._unique_entity_index([parsed, existing])
    with pytest.raises(ValueError, match="conflicting duplicate entity_id"):
        SemanticMerger._unique_entity_index([existing, parsed])


def test_semantic_universe_deduplicates_identical_entity_records():
    entity = _entity("ent_same", "pkg.same")
    result = SemanticMerger._unique_entity_index([entity, entity])
    assert result == {"ent_same": entity}


def test_semantic_universe_preserves_distinct_ids_without_reordering_identity():
    first = _entity("ent_a", "pkg.a")
    second = _entity("ent_b", "pkg.b")
    result = SemanticMerger._unique_entity_index([first, second])
    assert list(result) == ["ent_a", "ent_b"]
    assert result["ent_a"] is first
    assert result["ent_b"] is second
