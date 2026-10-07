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


@pytest.mark.parametrize("bad", [True, 1, "", "bad\x00parent"])
def test_malformed_source_parent_id_cannot_supply_same_class_authority(bad):
    entities = [
        _entity("ent_source", "source", EntityKind.METHOD, parent_id=bad),
        _entity("ent_target", "target", EntityKind.METHOD, parent_id=bad),
    ]
    with pytest.raises(ValueError, match="parent_id.*non-empty NUL-free text or null"):
        resolve_syntactic_call(_relation(), entities)


@pytest.mark.parametrize("bad", [True, 1, "", "bad\x00parent"])
def test_malformed_target_parent_id_fails_closed_during_structural_match(bad):
    entities = [
        _entity("ent_source", "source", EntityKind.METHOD, parent_id="ent_class"),
        _entity("ent_target", "target", EntityKind.METHOD, parent_id=bad),
    ]
    with pytest.raises(ValueError, match="parent_id.*non-empty NUL-free text or null"):
        resolve_syntactic_call(_relation(), entities)


def test_valid_parent_ids_preserve_same_class_resolution():
    entities = [
        _entity("ent_class", "Owner", EntityKind.CLASS),
        _entity("ent_source", "source", EntityKind.METHOD, parent_id="ent_class"),
        _entity("ent_target", "target", EntityKind.METHOD, parent_id="ent_class"),
    ]
    result = resolve_syntactic_call(_relation(), entities)
    assert len(result) == 1
    assert result[0].entity.entity_id == "ent_target"
    assert result[0].trust_class == TrustClass.HEURISTIC
    assert result[0].reason == "same_class_receiver_without_binding"
