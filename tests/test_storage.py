import os
import tempfile

from codeintel.core.models import (
    Entity,
    EntityKind,
    Relation,
    RelationType,
    TrustClass,
)
from codeintel.storage.sqlite_store import SQLiteStore


def setup_graph(store, repo_id, gen_id):
    e1 = Entity(
        entity_id="e_main", repo_id=repo_id, file_id="f1", generation_id=gen_id,
        name="main", qualified_name="app.main", kind=EntityKind.FUNCTION, span=(1, 0, 5, 0)
    )
    e2 = Entity(
        entity_id="e_auth", repo_id=repo_id, file_id="f2", generation_id=gen_id,
        name="authenticate", qualified_name="auth.authenticate", kind=EntityKind.FUNCTION, span=(1, 0, 10, 0)
    )
    e3 = Entity(
        entity_id="e_db", repo_id=repo_id, file_id="f3", generation_id=gen_id,
        name="query_user", qualified_name="db.query_user", kind=EntityKind.FUNCTION, span=(1, 0, 10, 0)
    )
    store.add_entities([e1, e2, e3])

    r1 = Relation(
        relation_id="r1", repo_id=repo_id, generation_id=gen_id,
        source_id="e_main", target_id="e_auth", rel_type=RelationType.CALLS,
        file_id="f1", span=(2, 4, 2, 16), trust_class=TrustClass.EXACT
    )
    r2 = Relation(
        relation_id="r2", repo_id=repo_id, generation_id=gen_id,
        source_id="e_auth", target_id="e_db", rel_type=RelationType.CALLS,
        file_id="f2", span=(5, 4, 5, 14), trust_class=TrustClass.EXACT
    )
    store.add_relations([r1, r2])


def test_sqlite_store_traversal():
    """The ranking substrate preserves caller/callee evidence."""
    with tempfile.TemporaryDirectory() as tmp:
        store = SQLiteStore(os.path.join(tmp, "test.db"))
        gen = store.create_generation("repo_test", 1, "hash1")
        store.activate_generation(gen.generation_id)
        setup_graph(store, "repo_test", gen.generation_id)

        ent = store.get_entity("e_auth")
        assert ent is not None
        assert ent.name == "authenticate"

        callers = store.get_callers("e_db")
        assert [(depth, entity.entity_id, trust) for depth, entity, trust in callers] == [(1, "e_auth", "EXACT")]

        callees = store.get_callees("e_main")
        assert [(depth, entity.entity_id, trust) for depth, entity, trust in callees] == [(1, "e_auth", "EXACT")]

        assert [(depth, entity.entity_id, trust) for depth, entity, trust in
                store.get_callers("e_auth", max_depth=1)] == [(1, "e_main", "EXACT")]
        assert [(depth, entity.entity_id, trust) for depth, entity, trust in
                store.get_callees("e_auth", max_depth=1)] == [(1, "e_db", "EXACT")]
        store.close()
