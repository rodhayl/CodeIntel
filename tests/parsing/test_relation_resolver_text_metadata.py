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


def _relation(**properties) -> Relation:
    base = {
        "callee_name": "target",
        "receiver_name": None,
        "receiver_type": None,
        "is_this": False,
        "enclosing_class_id": None,
    }
    base.update(properties)
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
        properties=base,
    )


def test_boolean_receiver_type_cannot_coerce_into_valid_class_name():
    entities = [
        _entity("ent_source", "source", EntityKind.FUNCTION, parent_id="ent_module"),
        _entity("ent_true", "True", EntityKind.CLASS, parent_id="ent_module"),
        _entity("ent_target", "target", EntityKind.METHOD, parent_id="ent_true"),
    ]
    with pytest.raises(ValueError, match="receiver_type.*text or null"):
        resolve_syntactic_call(_relation(receiver_type=True), entities)


def test_boolean_receiver_name_cannot_coerce_into_valid_class_name():
    entities = [
        _entity("ent_source", "source", EntityKind.FUNCTION, parent_id="ent_module"),
        _entity("ent_true", "True", EntityKind.CLASS, parent_id="ent_module"),
        _entity("ent_target", "target", EntityKind.METHOD, parent_id="ent_true"),
    ]
    with pytest.raises(ValueError, match="receiver_name.*text or null"):
        resolve_syntactic_call(_relation(receiver_name=True), entities)


def test_boolean_callee_cannot_coerce_into_valid_function_name():
    entities = [
        _entity("ent_source", "source", EntityKind.FUNCTION, parent_id="ent_module"),
        _entity("ent_true", "True", EntityKind.FUNCTION, parent_id="ent_module"),
    ]
    with pytest.raises(ValueError, match="callee_name.*text or null"):
        resolve_syntactic_call(_relation(callee_name=True), entities)


@pytest.mark.parametrize("bad", [True, 1, "", "bad\x00id"])
def test_enclosing_class_id_requires_nonempty_nul_free_text_or_null(bad):
    entities = [
        _entity("ent_source", "source", EntityKind.FUNCTION),
        _entity("ent_target", "target", EntityKind.METHOD, parent_id=bad),
    ]
    with pytest.raises(ValueError, match="enclosing_class_id"):
        resolve_syntactic_call(
            _relation(is_this=True, enclosing_class_id=bad),
            entities,
        )


def test_normal_text_metadata_preserves_explicit_receiver_type_resolution():
    entities = [
        _entity("ent_source", "source", EntityKind.FUNCTION, parent_id="ent_module"),
        _entity("ent_client", "Client", EntityKind.CLASS, parent_id="ent_module"),
        _entity("ent_target", "target", EntityKind.METHOD, parent_id="ent_client"),
    ]
    result = resolve_syntactic_call(_relation(receiver_type="Client"), entities)
    assert len(result) == 1
    assert result[0].entity.entity_id == "ent_target"
    assert result[0].trust_class == TrustClass.RESOLVED
    assert result[0].reason == "explicit_receiver_type"
