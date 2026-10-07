"""Transparent writes and type/deferred scopes cannot invent authoritative callees."""
import pytest

from codeintel.core.models import RelationType, TrustClass
from codeintel.lab.retrieval import index_repository, query_repository, verify_packet
from codeintel.parsing.parser_dispatch import CanonicalTreeSitterCodeParser
from codeintel.parsing.relation_resolver import resolve_syntactic_call
from codeintel.service.production import create_default_service


PREFIX = ("class Client { go() { return 'wrong_client'; } }\n"
          "class Other { go() { return 'right_other'; } }\n")


def _parse(source, path="example.ts"):
    return CanonicalTreeSitterCodeParser().parse_file(
        "repo", "file", "gen", path, PREFIX + source
    )


def _go_calls(relations):
    return [relation for relation in relations
            if relation.rel_type == RelationType.CALLS
            and relation.properties.get("callee_name") == "go"]


@pytest.mark.parametrize("target", [
    "(obj)", "((obj))", "(/* target */ obj)", "obj!", "(obj as Other)",
    "(<Other>obj)", "((obj! as Other))",
])
def test_transparent_assignment_target_invalidates_parameter_type(target):
    entities, relations, _ = _parse(
        f"function run(obj: Client) {{ {target} = new Other(); obj.go(); }}\n"
    )
    calls = _go_calls(relations)
    assert len(calls) == 1
    assert calls[0].properties.get("receiver_type") is None
    assert all(target.trust_class != TrustClass.RESOLVED
               for target in resolve_syntactic_call(calls[0], entities))


@pytest.mark.parametrize("path", ["example.ts", "example.tsx"])
@pytest.mark.parametrize("declaration", [
    "function run<Client extends Other>(obj: Client) { return obj.go(); }",
    "class Owner { run<Client extends Other>(obj: Client) { return obj.go(); } }",
    "class Owner<Client extends Other> { run(obj: Client) { return obj.go(); } }",
    "abstract class Owner<Client extends Other> { run(obj: Client) { return obj.go(); } }",
])
def test_type_parameter_name_cannot_bind_to_unrelated_concrete_class(path, declaration):
    entities, relations, _ = _parse(declaration, path)
    calls = _go_calls(relations)
    assert len(calls) == 1
    assert calls[0].properties.get("receiver_type") is None
    assert all(target.trust_class != TrustClass.RESOLVED
               for target in resolve_syntactic_call(calls[0], entities))


@pytest.mark.parametrize("declaration", [
    "class Inner { field = obj.go(); }",
    "abstract class Inner { field = obj.go(); }",
    "abstract class Inner { method() { return obj.go(); } }",
])
def test_nested_class_deferred_body_does_not_borrow_outer_callable(declaration):
    entities, relations, chunks = _parse(
        "function run(obj: Client) { " + declaration + " return 'no_call'; }\n"
    )
    assert not _go_calls(relations)
    assert not any(entity.name == "Inner" for entity in entities)
    assert any(declaration in chunk.content for chunk in chunks)


@pytest.mark.parametrize("declaration", [
    "function run(obj: Client) { return obj.go(); }",
    "function run<T extends Other>(obj: Client) { return obj.go(); }",
    "class Owner<T extends Other> { run(obj: Client) { return obj.go(); } }",
    "function run(obj: Client) { (obj.go) = () => 'updated'; return obj.go(); }",
])
def test_unshadowed_type_survives_unrelated_generics_and_member_writes(declaration):
    entities, relations, _ = _parse(declaration)
    calls = _go_calls(relations)
    assert len(calls) == 1
    assert calls[0].properties.get("receiver_type") == "Client"
    resolved = resolve_syntactic_call(calls[0], entities)
    assert [(target.entity.qualified_name, target.trust_class) for target in resolved] == [
        ("example.Client.go", TrustClass.RESOLVED)
    ]


@pytest.mark.parametrize("declaration", [
    "function run(obj: Client) { (obj) = new Other(); return obj.go(); }",
    "function run<Client extends Other>(obj: Client) { return obj.go(); }",
    "function run(obj: Client) { abstract class Inner { field = obj.go(); } return 'no_call'; }",
])
def test_invalid_scope_evidence_cannot_add_wrong_body_to_cli_packet(tmp_path, declaration):
    repo, state = tmp_path / "repo", tmp_path / "state"
    repo.mkdir()
    (repo / "example.ts").write_text(PREFIX + declaration + "\n")
    index_repository(repo, state)
    with create_default_service(str(repo), state_dir=str(state)) as service:
        generation = service.barrier.active_generation.generation_id
        run = service.graph_store.get_entities_by_name("run", generation_id=generation)[0]
        callees = service.graph_store.get_callees(run.entity_id, generation_id=generation)
        assert not any(entity.qualified_name == "example.Client.go"
                       for _, entity, *_ in callees)
    packet, _ = query_repository(repo, state, "run", max_bytes=8192, limit=20)
    verify_packet(repo, packet)
    assert not any("wrong_client" in row["source"] for row in packet["selected"])
