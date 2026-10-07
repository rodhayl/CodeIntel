from __future__ import annotations

import pytest

from codeintel.core.models import Entity, EntityKind, Relation, RelationType, TrustClass
from codeintel.parsing.relation_resolver import resolve_syntactic_call


def _entity(entity_id: str, name: str, kind: EntityKind, *, parent_id=None) -> Entity:
    properties = {} if parent_id is None else {"parent_id": parent_id}
    return Entity(
        entity_id=entity_id,
        repo_id="repo_1",
        file_id="file_1",
        generation_id="gen_1",
        name=name,
        qualified_name=f"pkg.{name}",
        kind=kind,
        span=(1, 0, 1, 1),
        properties=properties,
    )


def _relation(enclosing_class_id) -> Relation:
    return Relation(
        relation_id="rel_fixture",
        repo_id="repo_1",
        generation_id="gen_1",
        source_id="ent_source",
        target_id="ent_placeholder",
        rel_type=RelationType.CALLS,
        file_id="file_1",
        span=(1, 0, 1, 1),
        trust_class=TrustClass.EXACT,
        properties={
            "callee_name": "target",
            "is_this": True,
            "enclosing_class_id": enclosing_class_id,
        },
    )


def _entities():
    return [
        _entity("ent_class_a", "A", EntityKind.CLASS),
        _entity("ent_class_b", "B", EntityKind.CLASS),
        _entity("ent_source", "source", EntityKind.METHOD, parent_id="ent_class_a"),
        _entity("ent_target_a", "target", EntityKind.METHOD, parent_id="ent_class_a"),
        _entity("ent_target_b", "target", EntityKind.METHOD, parent_id="ent_class_b"),
    ]


def test_contradictory_enclosing_class_metadata_fails_closed():
    with pytest.raises(ValueError, match="contradicts.*source entity parent"):
        resolve_syntactic_call(_relation("ent_class_b"), _entities())


def test_matching_redundant_enclosing_class_still_resolves_canonical_target():
    result = resolve_syntactic_call(_relation("ent_class_a"), _entities())
    assert len(result) == 1
    assert result[0].entity.entity_id == "ent_target_a"
    assert result[0].trust_class == TrustClass.HEURISTIC
    assert result[0].reason == "same_class_receiver_without_binding"


def test_absent_redundant_enclosing_class_uses_source_parent():
    result = resolve_syntactic_call(_relation(None), _entities())
    assert len(result) == 1
    assert result[0].entity.entity_id == "ent_target_a"
    assert result[0].trust_class == TrustClass.HEURISTIC
