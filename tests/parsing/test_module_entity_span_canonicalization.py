from pathlib import Path

import pytest

from codeintel.core.models import EntityKind
from codeintel.parsing.semantic_merger import SemanticMerger


@pytest.mark.parametrize(
    ("rel_path", "content", "expected_span"),
    [
        ("src/example.py", "α = 1\n", (1, 0, 2, 0)),
        (
            "src/example.ts",
            "const α = 1;\nconst β = 2;",
            (1, 0, 2, len("const β = 2;".encode("utf-8"))),
        ),
        ("src/example.js", "const π = 3;", (1, 0, 1, len("const π = 3;".encode("utf-8")))),
    ],
)
def test_semantic_merger_module_entity_span_matches_literal_utf8_bytes(
    tmp_path: Path, rel_path: str, content: str, expected_span
):
    target = tmp_path / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8", newline="")

    entities, _relations, chunks, _status = SemanticMerger().parse_repository(
        repo_id="repo_test",
        repo_root=str(tmp_path),
        generation_id="gen_direct",
        file_paths=[rel_path],
    )

    modules = [entity for entity in entities if entity.kind == EntityKind.MODULE]
    assert len(modules) == 1
    module = modules[0]
    assert module.span == expected_span

    owning = [chunk for chunk in chunks if module.entity_id in chunk.entity_ids]
    assert owning
    assert owning[0].span == expected_span


def test_literal_file_span_handles_empty_source():
    assert SemanticMerger._literal_file_span("") == (1, 0, 1, 0)
