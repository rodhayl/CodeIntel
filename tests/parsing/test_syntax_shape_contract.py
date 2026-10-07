"""Bounded syntax coverage: explicit call owners, not compiler/runtime binding."""
from __future__ import annotations

import pytest

from codeintel.core.models import EntityKind, RelationType
from codeintel.parsing.chunking import bound_chunks
from codeintel.parsing.treesitter_parser import TreeSitterCodeParser


def _parse(source, path):
    return TreeSitterCodeParser().parse_file("repo", "file", "gen", path, source)


def _calls(entities, relations):
    names = {entity.entity_id: entity.qualified_name for entity in entities}
    return [(names[relation.source_id], relation.properties["callee_name"])
            for relation in relations if relation.rel_type == RelationType.CALLS]


def _assert_literal_partition(source, chunks):
    raw = source.encode("utf-8")
    starts = [0] + [index + 1 for index, value in enumerate(raw) if value == 10]
    cursor = 0
    for chunk in bound_chunks(chunks):
        sl, sc, el, ec = chunk.span
        start, end = starts[sl - 1] + sc, starts[el - 1] + ec
        assert start >= cursor
        assert not raw[cursor:start].strip()
        assert raw[start:end] == chunk.content.encode("utf-8")
        cursor = end
    assert not raw[cursor:].strip()


def test_python_lambda_body_does_not_borrow_outer_call_owner():
    source = (
        "def deferred(): return 1\n"
        "def direct(): return 2\n"
        "def nested(): return 3\n"
        "def factory():\n"
        "    direct()\n"
        "    def inner(): return nested()\n"
        "    return lambda: deferred()\n"
    )
    entities, relations, chunks = _parse(source, "sample.py")
    assert set(_calls(entities, relations)) == {
        ("sample.factory", "direct"), ("sample.factory.inner", "nested")
    }
    _assert_literal_partition(source, chunks)


@pytest.mark.parametrize('expression', [
    'lambda value=eager(): deferred()',
    'lambda *, value=eager(): deferred()',
    'lambda value=(lambda other=eager(): deferred()): deferred()',
])
def test_python_lambda_eager_defaults_keep_outer_owner(expression):
    source = ('def eager(): return 1\ndef deferred(): return 2\n'
              'def outer():\n    return ' + expression + '\n')
    entities, relations, chunks = _parse(source, 'defaults.py')
    assert _calls(entities, relations) == [('defaults.outer', 'eager')]
    _assert_literal_partition(source, chunks)


@pytest.mark.parametrize("path", ["sample.js", "sample.jsx", "sample.mjs", "sample.cjs", "sample.ts", "sample.tsx"])
@pytest.mark.parametrize("body", ["target()", "(target())", "{ return target(); }"])
def test_variable_arrow_body_has_its_own_call_owner(path, body):
    source = "function target() { return 1; }\nconst run = () => " + body + ";\n"
    entities, relations, chunks = _parse(source, path)
    run = next(entity for entity in entities if entity.name == "run")
    assert run.kind == EntityKind.FUNCTION
    assert _calls(entities, relations) == [("sample.run", "target")]
    _assert_literal_partition(source, chunks)


@pytest.mark.parametrize("path", ["sample.ts", "sample.tsx"])
@pytest.mark.parametrize("body", ["target()", "{ return target(); }"])
def test_typed_variable_arrow_uses_grammar_value_field(path, body):
    source = "function target() { return 1; }\nconst run: () => number = () => " + body + ";\n"
    entities, relations, chunks = _parse(source, path)
    assert any(entity.qualified_name == "sample.run" for entity in entities)
    assert _calls(entities, relations) == [("sample.run", "target")]
    _assert_literal_partition(source, chunks)


@pytest.mark.parametrize("body", ["() => deferred()", "() => { deferred(); }", "function () { deferred(); }"])
def test_returned_callable_body_is_not_attributed_to_arrow_owner(body):
    source = "const run = () => " + body + ";\n"
    entities, relations, chunks = _parse(source, "sample.ts")
    assert _calls(entities, relations) == []
    _assert_literal_partition(source, chunks)


@pytest.mark.parametrize("path", ["sample.js", "sample.ts", "sample.tsx"])
def test_nested_js_ts_declarations_remain_literal_only(path):
    # Deliberate bounded non-claim: no nested declaration entity/binding engine.
    source = "function outer() { function inner() { return deferred(); } return inner(); }\n"
    entities, relations, chunks = _parse(source, path)
    assert {entity.qualified_name for entity in entities} == {"sample", "sample.outer"}
    assert _calls(entities, relations) == [("sample.outer", "inner")]
    _assert_literal_partition(source, chunks)
    assert any("function inner()" in chunk.content for chunk in chunks)


@pytest.mark.parametrize("path", ["sample.ts", "sample.tsx"])
def test_interface_method_signatures_remain_literal_only(path):
    # Interface declarations have an entity; signatures do not imply a callable.
    source = "interface Client { go(): number; }\n"
    entities, relations, chunks = _parse(source, path)
    assert {(entity.qualified_name, entity.kind) for entity in entities} == {
        ("sample", EntityKind.MODULE), ("sample.Client", EntityKind.INTERFACE)
    }
    assert _calls(entities, relations) == []
    _assert_literal_partition(source, chunks)
    assert any("go(): number" in chunk.content for chunk in chunks)


@pytest.mark.parametrize(("path", "source", "expected_names"), [
    ("sample.py", "def target(): return 1\ndef direct(): return 2\ndef run():\n    direct()\n    return lambda: target()\n", {"direct"}),
    ("sample.ts", "function target() { return 1; }\nconst run: () => number = () => target();\n", {"target"}),
    ("sample.js", "function target() { return 1; }\nconst run = () => target();\n", {"target"}),
])
def test_persisted_graph_obeys_syntactic_call_owner(tmp_path, path, source, expected_names):
    from codeintel.lab.retrieval import index_repository, query_repository, verify_packet
    from codeintel.service.production import create_default_service

    repo, state = tmp_path / "repo", tmp_path / "state"
    repo.mkdir()
    (repo / path).write_text(source, encoding="utf-8", newline="")
    index_repository(repo, state)
    with create_default_service(str(repo), state_dir=str(state)) as service:
        generation = service.barrier.active_generation.generation_id
        run = service.graph_store.get_entities_by_name("run", generation_id=generation)[0]
        callees = service.graph_store.get_callees(run.entity_id, max_depth=1,
                                                 generation_id=generation, include_heuristic=True)
        assert {entity.name for _, entity, _ in callees} == expected_names
    packet, _ = query_repository(repo, state, "run", max_bytes=8192, limit=20)
    assert packet["status"] == "OK"
    verify_packet(repo, packet)
