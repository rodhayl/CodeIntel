from codeintel.core.models import Entity, EntityKind, Relation, RelationType, TrustClass
from codeintel.parsing.relation_resolver import resolve_syntactic_call


def test_javascript_prototype_receiver_resolves_exact_class_method():
    cls = Entity(
        entity_id="ent_class",
        repo_id="repo",
        file_id="file",
        generation_id="gen",
        name="Foo",
        qualified_name="Foo",
        kind=EntityKind.CLASS,
        span=(1, 0, 10, 0),
    )
    method = Entity(
        entity_id="ent_method",
        repo_id="repo",
        file_id="file",
        generation_id="gen",
        name="bar",
        qualified_name="Foo.bar",
        kind=EntityKind.METHOD,
        span=(2, 0, 4, 0),
        properties={"parent_id": cls.entity_id},
    )
    relation = Relation(
        relation_id="rel",
        repo_id="repo",
        generation_id="gen",
        source_id="caller",
        target_id="synthetic",
        rel_type=RelationType.CALLS,
        file_id="file",
        span=(12, 0, 12, 18),
        properties={"callee_name": "bar", "receiver_name": "Foo.prototype"},
    )

    resolved = resolve_syntactic_call(relation, [cls, method])

    assert len(resolved) == 1
    assert resolved[0].entity.entity_id == method.entity_id
    assert resolved[0].trust_class == TrustClass.HEURISTIC
    assert resolved[0].reason == "class_name_receiver_without_binding"
