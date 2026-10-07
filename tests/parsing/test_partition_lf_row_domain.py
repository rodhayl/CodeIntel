"""Canonical source ownership must share Tree-sitter's LF-only row domain."""

from pathlib import Path

import pytest

from codeintel.core.models import EntityKind
from codeintel.lab.retrieval import literal_span
from codeintel.parsing.parser_dispatch import CanonicalTreeSitterCodeParser
from codeintel.parsing.semantic_merger import SemanticMerger


def _source(path, separator, final_newline):
    if path.endswith(".py"):
        definition = "def target():\n    return 1"
        source = 'marker = """a' + separator + 'b"""\n' + definition
    else:
        definition = "function target() { return 1; }"
        source = "const marker = `a" + separator + "b`;\n" + definition
    return source + ("\n" if final_newline else ""), definition


@pytest.mark.parametrize("path", ["sample.py", "sample.js", "sample.ts"])
@pytest.mark.parametrize("separator", ["\r", "\r\n", "\n", "\v", "\f", "\x85", "\u2028", "\u2029"])
def test_partition_preserves_definition_ownership_across_non_lf_separators(path, separator):
    source, definition = _source(path, separator, True)
    entities, _, chunks = CanonicalTreeSitterCodeParser().parse_file(
        "repo_partition", "file_partition", "gen_partition", path, source
    )
    target = next(entity for entity in entities if entity.name == "target")
    owning = [chunk for chunk in chunks if target.entity_id in chunk.entity_ids]
    assert len(owning) == 1
    assert owning[0].content == definition
    raw = source.encode("utf-8")
    assert literal_span(raw, target.span) == definition.encode("utf-8")
    for chunk in chunks:
        assert literal_span(raw, chunk.span) == chunk.content.encode("utf-8")
    module = next(entity for entity in entities if entity.kind == EntityKind.MODULE)
    assert any(separator in chunk.content and chunk.entity_ids == [module.entity_id] for chunk in chunks)


@pytest.mark.parametrize("path", ["sample.py", "sample.js", "sample.ts"])
@pytest.mark.parametrize("final_newline", [False, True])
def test_merger_cr_literal_ownership_and_eof_span_round_trip(tmp_path: Path, path, final_newline):
    source, definition = _source(path, "\r", final_newline)
    (tmp_path / path).write_text(source, encoding="utf-8", newline="")
    entities, _, chunks, _ = SemanticMerger().parse_repository(
        "repo_partition", str(tmp_path), "gen_partition", [path]
    )
    target = next(entity for entity in entities if entity.name == "target")
    assert [chunk.content for chunk in chunks if target.entity_id in chunk.entity_ids] == [definition]
    for chunk in chunks:
        assert literal_span(source.encode("utf-8"), chunk.span) == chunk.content.encode("utf-8")
