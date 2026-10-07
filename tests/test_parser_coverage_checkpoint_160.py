from __future__ import annotations

from typing import Iterable

import pytest

from codeintel.core.models import EntityKind
from codeintel.parsing.chunk_partition import partition_entity_chunks
from codeintel.parsing.parser_dispatch import CanonicalTreeSitterCodeParser


def _line_starts(source: bytes) -> list[int]:
    starts = [0]
    for index, value in enumerate(source):
        if value == 0x0A:
            starts.append(index + 1)
    return starts


def _assert_partition(content: str, chunks: Iterable[object]) -> None:
    source = content.encode("utf-8")
    starts = _line_starts(source)
    intervals = []
    for chunk in chunks:
        line, col = chunk.span[0], chunk.span[1]
        assert 1 <= line <= len(starts)
        start = starts[line - 1] + col
        encoded = chunk.content.encode("utf-8")
        end = start + len(encoded)
        assert source[start:end] == encoded
        intervals.append((start, end, chunk))
    intervals.sort(key=lambda item: (item[0], item[1], item[2].chunk_id))
    cursor = 0
    for start, end, _chunk in intervals:
        assert start >= cursor, f"overlapping retrieval chunks at byte {start}"
        assert not source[cursor:start].strip(), f"lost source bytes {cursor}:{start}"
        cursor = end
    assert not source[cursor:].strip(), f"lost trailing source bytes {cursor}:{len(source)}"


def _parse(content: str, rel_path: str = "sample.py"):
    parser = CanonicalTreeSitterCodeParser()
    return parser.parse_file("repo", "file_sample", "gen_sample", rel_path, content)


def _entity(entities, kind: EntityKind, name: str):
    matches = [entity for entity in entities if entity.kind == kind and entity.name == name]
    assert len(matches) == 1
    return matches[0]


def test_module_source_between_and_after_definitions_is_not_lost():
    content = (
        "HEADER = 1\n"
        "def first():\n"
        "    return 1\n"
        "BETWEEN = 2\n"
        "def second():\n"
        "    return 2\n"
        "TAIL = 3\n"
    )
    entities, _relations, chunks = _parse(content)
    _assert_partition(content, chunks)
    module = next(entity for entity in entities if entity.kind == EntityKind.MODULE)
    for marker in ("HEADER = 1", "BETWEEN = 2", "TAIL = 3"):
        owners = [chunk for chunk in chunks if marker in chunk.content]
        assert len(owners) == 1
        assert owners[0].entity_ids == [module.entity_id]


def test_class_attributes_between_and_after_methods_remain_class_owned():
    content = (
        "class Example:\n"
        "    BEFORE = 1\n"
        "    def one(self):\n"
        "        return self.BEFORE\n"
        "    BETWEEN = 2\n"
        "    def two(self):\n"
        "        return self.BETWEEN\n"
        "    AFTER = 3\n"
    )
    entities, _relations, chunks = _parse(content)
    _assert_partition(content, chunks)
    cls = _entity(entities, EntityKind.CLASS, "Example")
    for marker in ("BEFORE = 1", "BETWEEN = 2", "AFTER = 3"):
        owners = [chunk for chunk in chunks if marker in chunk.content]
        assert len(owners) == 1
        assert owners[0].entity_ids == [cls.entity_id]


def test_nested_definition_partitions_outer_function_without_overlap():
    content = (
        "def outer():\n"
        "    before = 1\n"
        "    def inner():\n"
        "        return 7\n"
        "    after = inner()\n"
        "    return before + after\n"
    )
    entities, _relations, chunks = _parse(content)
    _assert_partition(content, chunks)
    outer = _entity(entities, EntityKind.FUNCTION, "outer")
    inner = _entity(entities, EntityKind.FUNCTION, "inner")
    assert any("before = 1" in chunk.content and chunk.entity_ids == [outer.entity_id] for chunk in chunks)
    assert any("after = inner()" in chunk.content and chunk.entity_ids == [outer.entity_id] for chunk in chunks)
    assert any("def inner" in chunk.content and chunk.entity_ids == [inner.entity_id] for chunk in chunks)


def test_utf8_and_crlf_partition_uses_tree_sitter_byte_columns():
    content = (
        "π = 'é'\r\n"
        "def café():\r\n"
        "    return 'λ'\r\n"
        "尾 = café()\r\n"
    )
    _entities, _relations, chunks = _parse(content)
    _assert_partition(content, chunks)
    joined = "".join(chunk.content for chunk in chunks)
    for marker in ("π", "é", "café", "λ", "尾"):
        assert marker in joined


def test_missing_parent_entity_fails_closed_in_partition():
    content = "def first():\n    return 1\n"
    entities, _relations, chunks = _parse(content)
    fn = _entity(entities, EntityKind.FUNCTION, "first")
    fn.properties["parent_id"] = "ent_missing_parent"
    with pytest.raises(ValueError, match="missing parent"):
        partition_entity_chunks(
            entities=entities,
            content=content,
            rel_path="sample.py",
            file_id="file_sample",
            generation_id="gen_sample",
        )


def test_javascript_dispatch_retains_module_statements_around_functions():
    content = (
        "const before = 1;\n"
        "function alpha() { return before; }\n"
        "const between = 2;\n"
        "function beta() { return between; }\n"
        "const after = alpha() + beta();\n"
    )
    entities, _relations, chunks = _parse(content, rel_path="sample.js")
    _assert_partition(content, chunks)
    module = next(entity for entity in entities if entity.kind == EntityKind.MODULE)
    for marker in ("const before", "const between", "const after"):
        owners = [chunk for chunk in chunks if marker in chunk.content]
        assert len(owners) == 1
        assert owners[0].entity_ids == [module.entity_id]
