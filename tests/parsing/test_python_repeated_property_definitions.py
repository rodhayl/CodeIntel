"""Repeated property syntax retains every declaration and literal source range."""
import ast

import pytest

from codeintel.core.models import RelationType
from codeintel.lab.retrieval import index_repository, query_repository, verify_packet
from codeintel.parsing.parser_dispatch import CanonicalTreeSitterCodeParser

PROPERTY_CASES = {
    "mixed": '''class Client:
    @property
    def value(self): return 1
    def value(self): return 2
''',
    "repeated": '''class Client:
    @property
    def value(self): return 1
    @property
    def value(self): return 2
''',
    "conditional": '''class Client:
    if True:
        @property
        def value(self): return 1
    else:
        @property
        def value(self): return 2
''',
    "repeated_groups": '''class Client:
    @property
    def value(self): return 1
    @value.setter
    def value(self, v): self._value = v
    @property
    def value(self): return 2
    @value.setter
    def value(self, v): self._value = v + 1
''',
    "nested": '''class Client:
    @property
    def value(self):
        def helper(): return 1
        return helper()
    @property
    def value(self):
        def helper(): return 2
        return helper()
''',
}


def parse(source):
    return CanonicalTreeSitterCodeParser().parse_file("repo", "file", "gen", "sample.py", source)


@pytest.mark.parametrize("case", list(PROPERTY_CASES))
def test_disjoint_property_declarations_keep_distinct_source_identity(case):
    source = PROPERTY_CASES[case]
    ast.parse(source)
    entities, relations, chunks = parse(source)
    entity_ids = {entity.entity_id for entity in entities}
    assert len(entity_ids) == len(entities)
    declarations = [e for e in entities if e.qualified_name == "sample.Client.value"]
    expected_count = 4 if case == "repeated_groups" else 2
    assert len(declarations) == expected_count
    assert len({e.entity_id for e in declarations}) == expected_count
    assert all(any(e.entity_id in c.entity_ids for c in chunks) for e in declarations)
    for relation in relations:
        if relation.rel_type == RelationType.DEFINES:
            assert relation.source_id in entity_ids
            assert relation.target_id in entity_ids
    raw = source.encode()
    starts = [0] + [i + 1 for i, b in enumerate(raw) if b == 10]
    intervals = []
    for chunk in chunks:
        sl, sc, el, ec = chunk.span
        start = starts[sl - 1] + sc
        end = starts[el - 1] + ec
        assert raw[start:end] == chunk.content.encode()
        intervals.append((start, end))
    cursor = 0
    for start, end in sorted(intervals):
        assert start >= cursor
        assert not raw[cursor:start].strip()
        cursor = end
    assert not raw[cursor:].strip()
    changed_entities, _, _ = parse(source.replace("return 1", "return 11").replace("return 2", "return 22"))
    assert [e.entity_id for e in changed_entities] == [e.entity_id for e in entities]
    if case == "nested":
        helpers = [e for e in entities if e.name == "helper"]
        assert len(helpers) == 2
        assert {e.properties["parent_id"] for e in helpers} == {e.entity_id for e in declarations}


@pytest.mark.parametrize("case", list(PROPERTY_CASES))
def test_property_index_and_query_retain_every_declared_literal(tmp_path, case):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "sample.py").write_text(PROPERTY_CASES[case])
    state = tmp_path / "state"
    index_repository(repo, state)
    packet, _ = query_repository(repo, state, "Client.value", max_bytes=8192, limit=20)
    verify_packet(repo, packet)
    locations = [location for row in packet["selected"] for location in [row, *row["aliases"]]]
    symbols = {tuple(location["coverage"]["symbol_span"]) for location in locations if location["coverage"]["symbol_span"]}
    assert len(symbols) == (4 if case == "repeated_groups" else 2)
    assert all(location["coverage"]["complete"] for location in locations if location["coverage"]["symbol_span"])
