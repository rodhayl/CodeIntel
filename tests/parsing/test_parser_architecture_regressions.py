"""Small language/identity cases missing from the original parser coverage."""

from pathlib import Path

import pytest

from codeintel.core.models import EntityKind, RelationType, TrustClass
from codeintel.parsing.parser_dispatch import CanonicalTreeSitterCodeParser
from codeintel.parsing.relation_resolver import resolve_syntactic_call
from codeintel.parsing.relation_resolver import ResolvedCallTarget
from codeintel.parsing.semantic_merger import SemanticMerger


def _parse(source: str, path: str):
    return CanonicalTreeSitterCodeParser().parse_file(
        "repo_architecture", "file_architecture", "gen_architecture", path, source
    )


def _assert_literal_partition(source, entities, relations, chunks):
    raw = source.encode("utf-8")
    starts = [0] + [i + 1 for i, value in enumerate(raw) if value == 10]

    def offset(point):
        row, column = point
        assert 1 <= row <= len(starts)
        return starts[row - 1] + column

    entity_ids = {entity.entity_id for entity in entities}
    assert len(entity_ids) == len(entities)
    intervals = []
    for chunk in chunks:
        start, end = offset(chunk.span[:2]), offset(chunk.span[2:])
        assert raw[start:end] == chunk.content.encode("utf-8")
        assert set(chunk.entity_ids) <= entity_ids
        intervals.append((start, end))
    cursor = 0
    for start, end in sorted(intervals):
        assert start >= cursor
        assert not raw[cursor:start].strip()
        cursor = end
    assert not raw[cursor:].strip()
    for relation in relations:
        if relation.rel_type == RelationType.DEFINES:
            assert relation.source_id in entity_ids
            assert relation.target_id in entity_ids


@pytest.mark.parametrize("path", ["sample.ts", "sample.tsx"])
def test_typescript_overload_declarations_have_distinct_source_identities(path):
    source = (
        "function convert(x: string): string;\n"
        "function convert(x: number): number;\n"
        "function convert(x: any): any { return x; }\n"
    )
    entities, relations, chunks = _parse(source, path)
    declarations = [entity for entity in entities if entity.name == "convert"]
    assert len(declarations) == 3
    assert len({entity.entity_id for entity in declarations}) == 3
    assert len({entity.qualified_name for entity in declarations}) == 1
    _assert_literal_partition(source, entities, relations, chunks)
    changed, _, _ = _parse(source.replace("return x;", "return (x);"), path)
    assert [entity.entity_id for entity in changed] == [entity.entity_id for entity in entities]


@pytest.mark.parametrize("path", ["sample.js", "sample.jsx", "sample.ts", "sample.tsx", "sample.mjs", "sample.cjs"])
def test_javascript_typescript_accessors_have_distinct_source_identities(path):
    source = (
        "class Client {\n"
        " get value() { return read(); }\n"
        " set value(v) { write(v); }\n"
        "}\n"
    )
    entities, relations, chunks = _parse(source, path)
    accessors = [entity for entity in entities if entity.name == "value"]
    assert len(accessors) == 2
    assert len({entity.entity_id for entity in accessors}) == 2
    assert len({entity.qualified_name for entity in accessors}) == 1
    assert {entity.properties.get("accessor_role") for entity in accessors} == {"getter", "setter"}
    assert {relation.source_id for relation in relations if relation.rel_type == RelationType.CALLS} >= {
        entity.entity_id for entity in accessors
    }
    _assert_literal_partition(source, entities, relations, chunks)


@pytest.mark.parametrize("path", ["sample.js", "sample.ts"])
@pytest.mark.parametrize("separator", ["\u2028", "\u2029", "\x85"])
def test_non_lf_string_separators_do_not_change_syntax_byte_rows(path, separator):
    source = f'const marker = "a{separator}b";\nfunction target() {{ return 1; }}\n'
    entities, relations, chunks = _parse(source, path)
    target = next(entity for entity in entities if entity.name == "target")
    assert target.span == (2, 0, 2, len("function target() { return 1; }"))
    _assert_literal_partition(source, entities, relations, chunks)


@pytest.mark.parametrize("separator", ["\u2028", "\u2029", "\x85"])
def test_python_property_decorator_rows_are_lf_only(separator):
    source = (
        f'marker = "a{separator}b"\n'
        "class Client:\n"
        "    @property\n"
        "    def value(self): return 1\n"
        "    @value.setter\n"
        "    def value(self, v): self._value = v\n"
    )
    entities, relations, chunks = _parse(source, "sample.py")
    accessors = [entity for entity in entities if entity.name == "value"]
    assert {entity.properties.get("python_accessor_role") for entity in accessors} == {"getter", "setter"}
    _assert_literal_partition(source, entities, relations, chunks)


def _call_resolution(source, caller_name, path="sample.py"):
    entities, relations, _ = _parse(source, path)
    caller = next(entity for entity in entities if entity.name == caller_name)
    calls = [relation for relation in relations if relation.rel_type == RelationType.CALLS and relation.source_id == caller.entity_id]
    assert calls
    return [target for relation in calls for target in resolve_syntactic_call(relation, entities)]


def test_out_of_scope_nested_python_callable_is_not_a_target():
    source = (
        "def outer():\n"
        "    def hidden(): return 1\n"
        "    return hidden()\n"
        "def unrelated():\n"
        "    return hidden()\n"
    )
    assert _call_resolution(source, "unrelated") == []


@pytest.mark.parametrize(
    ("path", "source"),
    [
        ("sample.py", "def target(): return 1\ndef caller(target): return target()\n"),
        ("sample.ts", "function target() { return 1; }\nfunction caller(target: () => number) { return target(); }\n"),
    ],
)
def test_name_only_call_with_parameter_shadowing_has_no_authoritative_binding(path, source):
    resolved = _call_resolution(source, "caller", path)
    assert all(target.trust_class == TrustClass.HEURISTIC for target in resolved)


def test_self_parameter_on_top_level_function_is_not_enclosing_class_evidence():
    source = "def target(): return 1\ndef caller(self): return self.target()\n"
    assert _call_resolution(source, "caller") == []


def test_actual_same_class_method_is_a_structural_candidate_not_binding_proof():
    source = "class Client:\n    def target(self): return 1\n    def caller(self): return self.target()\n"
    resolved = _call_resolution(source, "caller")
    assert len(resolved) == 1
    assert resolved[0].entity.name == "target"
    assert resolved[0].trust_class == TrustClass.HEURISTIC


def test_visible_nested_callable_remains_only_a_name_candidate():
    source = "def outer():\n    def inner(): return 1\n    return inner()\n"
    resolved = _call_resolution(source, "outer")
    assert len(resolved) == 1
    assert resolved[0].entity.qualified_name == "sample.outer.inner"
    assert resolved[0].trust_class == TrustClass.HEURISTIC


@pytest.mark.parametrize("method", [
    "    @staticmethod\n    def caller(self): return self.target()\n",
    "    def caller(self, other):\n        self = other\n        return self.target()\n",
])
def test_python_receiver_name_is_not_binding_proof(method):
    source = "class Client:\n    def target(self): return 1\n" + method
    resolved = _call_resolution(source, "caller")
    assert resolved
    assert all(target.trust_class == TrustClass.HEURISTIC for target in resolved)


def test_exact_class_receiver_name_can_be_shadowed():
    source = "class Client:\n    def target(self): return 1\ndef caller(Client): return Client.target()\n"
    resolved = _call_resolution(source, "caller")
    assert resolved
    assert all(target.trust_class == TrustClass.HEURISTIC for target in resolved)


def test_semantic_merger_accepts_valid_overloads_and_accessors(tmp_path: Path):
    sources = {
        "overload.ts": "function convert(x: string): string;\nfunction convert(x: any) { return x; }\n",
        "accessor.js": "class Client { get value() { return 1; } set value(v) {} }\n",
    }
    for path, content in sources.items():
        (tmp_path / path).write_text(content, encoding="utf-8", newline="")
    entities, relations, chunks, status = SemanticMerger().parse_repository(
        "repo_architecture", str(tmp_path), "gen_architecture", list(sources)
    )
    assert status == "SYNTAX_ONLY"
    assert len({entity.entity_id for entity in entities}) == len(entities)
    assert chunks
    assert relations


@pytest.mark.parametrize(
    "body",
    ["obj.go();\n client.go();", "client.go();\n obj.go();", "obj.go();\n obj.go();"],
)
def test_merger_preserves_each_call_site_and_its_own_trust(tmp_path: Path, body):
    source = "class Client { go() {} }\nfunction run(obj: Client) {\n " + body + "\n}\n"
    (tmp_path / "source.ts").write_text(source, encoding="utf-8")
    entities, relations, _, _ = SemanticMerger().parse_repository(
        "repo_architecture", str(tmp_path), "gen_architecture", ["source.ts"]
    )
    source_id = next(entity.entity_id for entity in entities if entity.name == "run")
    calls = [relation for relation in relations if relation.rel_type == RelationType.CALLS and relation.source_id == source_id]
    assert len(calls) == 2
    assert len({relation.relation_id for relation in calls}) == 2
    assert {relation.span[0] for relation in calls} == {3, 4}
    for relation in calls:
        if relation.properties["receiver_name"] == "obj":
            assert relation.trust_class == TrustClass.RESOLVED
            assert relation.properties["reason"] == "explicit_receiver_type"
        else:
            assert relation.trust_class == TrustClass.HEURISTIC


def test_merger_rejects_conflicting_evidence_for_one_relation_id(tmp_path: Path, monkeypatch):
    source = "def target(): return 1\ndef caller(): return target()\n"
    (tmp_path / "source.py").write_text(source, encoding="utf-8")

    def conflicting(relation, index):
        target = index.targets_by_name["target"][0]
        return [
            ResolvedCallTarget(target, TrustClass.RESOLVED, "first"),
            ResolvedCallTarget(target, TrustClass.HEURISTIC, "second"),
        ]

    monkeypatch.setattr("codeintel.parsing.semantic_merger.resolve_syntactic_call", conflicting)
    with pytest.raises(ValueError, match="conflicting duplicate resolved relation_id"):
        SemanticMerger().parse_repository(
            "repo_architecture", str(tmp_path), "gen_architecture", ["source.py"]
        )


@pytest.mark.parametrize(("path", "grammar"), [
    ("sample.js", "_js_lang"), ("sample.jsx", "_js_lang"),
    ("sample.mjs", "_js_lang"), ("sample.cjs", "_js_lang"),
    ("sample.ts", "_ts_lang"), ("sample.tsx", "_tsx_lang"),
])
def test_canonical_dispatch_selects_the_matching_grammar(path, grammar, monkeypatch):
    parser = CanonicalTreeSitterCodeParser()
    original = parser._parse_typescript
    seen = []

    def capture(*args, **kwargs):
        seen.append(args[-1].language)
        return original(*args, **kwargs)

    monkeypatch.setattr(parser, "_parse_typescript", capture)
    parser.parse_file("repo", "file", "gen", path, "function target() {}\n")
    assert seen == [getattr(parser, grammar)]


def test_syntax_error_retains_literal_source_without_claiming_complete_symbols():
    source = "def valid(): return 1\ndef broken(\n"
    entities, relations, chunks = _parse(source, "sample.py")
    assert any(entity.name == "valid" for entity in entities)
    assert any("def broken(" in chunk.content for chunk in chunks)
    _assert_literal_partition(source, entities, relations, chunks)
