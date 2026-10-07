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


def _relation(is_this) -> Relation:
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
            "receiver_name": None,
            "receiver_type": None,
            "is_this": is_this,
            "enclosing_class_id": "ent_class",
        },
    )


def _entities():
    return [
        _entity("ent_class", "Owner", EntityKind.CLASS),
        _entity("ent_source", "source", EntityKind.METHOD, parent_id="ent_class"),
        _entity("ent_target", "target", EntityKind.METHOD, parent_id="ent_class"),
    ]


@pytest.mark.parametrize("bad", ["false", "0", 1, 0, [], {}])
def test_non_boolean_is_this_metadata_fails_closed(bad):
    with pytest.raises(ValueError, match="is_this.*boolean"):
        resolve_syntactic_call(_relation(bad), _entities())


def test_true_is_this_still_resolves_same_enclosing_class():
    result = resolve_syntactic_call(_relation(True), _entities())
    assert len(result) == 1
    assert result[0].entity.entity_id == "ent_target"
    assert result[0].trust_class == TrustClass.HEURISTIC
    assert result[0].reason == "same_class_receiver_without_binding"


def test_false_is_this_does_not_gain_same_class_authority():
    result = resolve_syntactic_call(_relation(False), _entities())
    assert result == []
