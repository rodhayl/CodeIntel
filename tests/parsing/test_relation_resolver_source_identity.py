from __future__ import annotations

import pytest

from codeintel.core.models import Entity, EntityKind, Relation, RelationType, TrustClass
from codeintel.parsing.relation_resolver import CallResolutionIndex, resolve_syntactic_call


def _entity(entity_id: str, name: str, kind: EntityKind, *, parent_id=None, qname=None) -> Entity:
    properties = {} if parent_id is None else {"parent_id": parent_id}
    return Entity(
        entity_id=entity_id,
        repo_id="repo_1",
        file_id="file_1",
        generation_id="gen_1",
        name=name,
        qualified_name=qname or f"pkg.{name}",
        kind=kind,
        span=(1, 0, 1, 1),
        properties=properties,
    )


def _relation() -> Relation:
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
        properties={"callee_name": "target", "is_this": True},
    )


def _base_entities():
    class_a = _entity("ent_class_a", "A", EntityKind.CLASS)
    class_b = _entity("ent_class_b", "B", EntityKind.CLASS)
    target = _entity("ent_target", "target", EntityKind.METHOD, parent_id="ent_class_a")
    source_a = _entity(
        "ent_source", "source", EntityKind.METHOD, parent_id="ent_class_a", qname="pkg.A.source"
    )
    source_b = _entity(
        "ent_source", "source", EntityKind.METHOD, parent_id="ent_class_b", qname="pkg.B.source"
    )
    return class_a, class_b, target, source_a, source_b


def test_conflicting_duplicate_source_entity_fails_closed_instead_of_list_order_resolution():
    class_a, class_b, target, source_a, source_b = _base_entities()
    with pytest.raises(ValueError, match="Conflicting duplicate entity_id"):
        resolve_syntactic_call(
            _relation(), [class_a, class_b, source_a, source_b, target]
        )
    with pytest.raises(ValueError, match="Conflicting duplicate entity_id"):
        resolve_syntactic_call(
            _relation(), [class_a, class_b, source_b, source_a, target]
        )


def test_identical_duplicate_source_entity_is_deduplicated_without_false_conflict():
    class_a, _class_b, target, source_a, _source_b = _base_entities()
    result = resolve_syntactic_call(
        _relation(), [class_a, source_a, source_a, target]
    )
    assert len(result) == 1
    assert result[0].entity.entity_id == "ent_target"
    assert result[0].trust_class == TrustClass.HEURISTIC
    assert result[0].reason == "same_class_receiver_without_binding"


def test_prebuilt_resolution_index_preserves_result_without_reiterating_entities():
    class_a, _class_b, target, source_a, _source_b = _base_entities()
    entities = [class_a, source_a, target]
    expected = resolve_syntactic_call(_relation(), entities)

    class CountingEntities:
        iterations = 0

        def __iter__(self):
            self.iterations += 1
            return iter(entities)

    counted = CountingEntities()
    index = CallResolutionIndex.build(counted)
    assert counted.iterations == 1
    for _ in range(5):
        assert resolve_syntactic_call(_relation(), index) == expected
    assert counted.iterations == 1
