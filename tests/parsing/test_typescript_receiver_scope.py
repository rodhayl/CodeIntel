"""Typed receiver evidence cannot cross shadowing or nested callable scopes."""
import pytest

from codeintel.core.models import RelationType
from codeintel.lab.retrieval import index_repository, query_repository, verify_packet
from codeintel.parsing.parser_dispatch import CanonicalTreeSitterCodeParser
from codeintel.service.production import create_default_service


PREFIX = "class Client { go() { return 'client'; } }\nclass Other { go() { return 'other'; } }\n"


def _source(body):
    return PREFIX + "function run(obj: Client) {\n" + body + "\n}\n"


def _parse(body):
    return CanonicalTreeSitterCodeParser().parse_file(
        repo_id="repo", file_id="file", generation_id="gen", rel_path="example.ts",
        content=_source(body),
    )


@pytest.mark.parametrize("body", [
    "{ const obj: Other = new Other(); obj.go(); }",
    "{ const { value: obj } = { value: new Other() }; obj.go(); }",
    "try { throw new Other(); } catch (obj) { obj.go(); }",
    "for (const obj of [new Other()]) { obj.go(); }",
    "obj = new Other(); obj.go();",
    "{ class obj { static go() { return 'local'; } } obj.go(); }",
    "{ function obj() {} obj.go = () => 1; obj.go(); }",
])
def test_shadowed_or_reassigned_receiver_loses_outer_parameter_type(body):
    _, relations, _ = _parse(body)
    calls = [r for r in relations if r.rel_type == RelationType.CALLS
             and r.properties.get("callee_name") == "go"]
    assert calls
    assert all(r.properties.get("receiver_type") is None for r in calls)


@pytest.mark.parametrize("body", [
    "const callback = (obj: Other) => { obj.go(); };",
    "const callback = function (obj: Other) { obj.go(); };",
    "const callback = function* (obj: Other) { obj.go(); };",
])
def test_nested_callable_body_is_not_attributed_to_enclosing_function(body):
    _, relations, _ = _parse(body)
    assert not [r for r in relations if r.rel_type == RelationType.CALLS
                and r.properties.get("callee_name") == "go"]


def test_unshadowed_receiver_keeps_explicit_parameter_type():
    _, relations, _ = _parse("obj.go();")
    calls = [r for r in relations if r.rel_type == RelationType.CALLS]
    assert len(calls) == 1
    assert calls[0].properties["receiver_type"] == "Client"


@pytest.mark.parametrize("body", [
    "{ const obj: Other = new Other(); obj.go(); }",
    "const callback = (obj: Other) => { obj.go(); }; callback(new Other());",
])
def test_scope_mismatch_cannot_promote_wrong_callee_into_packet(tmp_path, body):
    repo, state = tmp_path / "repo", tmp_path / "state"
    repo.mkdir()
    (repo / "example.ts").write_text(_source(body))
    index_repository(repo, state)
    with create_default_service(str(repo), state_dir=str(state)) as service:
        generation = service.barrier.active_generation.generation_id
        run = service.graph_store.get_entities_by_name("run", generation_id=generation)[0]
        callees = service.graph_store.get_callees(run.entity_id, generation_id=generation)
        assert not any(entity.qualified_name == "example.Client.go" for _, entity, *_ in callees)
    packet, _ = query_repository(repo, state, "run", max_bytes=8192, limit=20)
    verify_packet(repo, packet)
    assert not any("return 'client'" in row["source"] for row in packet["selected"])
