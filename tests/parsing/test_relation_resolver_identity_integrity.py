import pytest

from codeintel.core.models import Entity, EntityKind, Relation, RelationType
from codeintel.parsing.relation_resolver import resolve_syntactic_call


def _entity(entity_id: str, qname: str, span):
    return Entity(
        entity_id=entity_id,
        repo_id="repo",
        file_id="file",
        generation_id="gen",
        name="foo",
        qualified_name=qname,
        kind=EntityKind.FUNCTION,
        span=span,
    )


def test_relation_resolver_rejects_conflicting_duplicate_entity_identity():
    relation = Relation(
        relation_id="rel",
        repo_id="repo",
        generation_id="gen",
        source_id="source",
        target_id="synthetic",
        rel_type=RelationType.CALLS,
        file_id="file",
        span=(1, 0, 1, 5),
        properties={"callee_name": "foo"},
    )
    entities = [
        _entity("ent_same", "a.foo", (1, 0, 2, 0)),
        _entity("ent_same", "b.foo", (5, 0, 6, 0)),
    ]

    with pytest.raises(ValueError, match="Conflicting duplicate entity_id"):
        resolve_syntactic_call(relation, entities)
