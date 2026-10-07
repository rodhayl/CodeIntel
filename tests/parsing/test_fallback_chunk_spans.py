from codeintel.parsing.parser_dispatch import CanonicalTreeSitterCodeParser


def _chunk_for(content: str):
    parser = CanonicalTreeSitterCodeParser()
    entities, relations, chunks = parser.parse_file(
        repo_id="repo",
        file_id="file",
        generation_id="gen",
        rel_path="README.md",
        content=content,
    )
    assert entities == []
    assert relations == []
    assert len(chunks) == 1
    return chunks[0]


def test_fallback_span_uses_exact_utf8_end_column():
    chunk = _chunk_for("é")
    assert chunk.span == (1, 0, 1, 2)
    assert chunk.content.encode("utf-8") == "é".encode("utf-8")


def test_fallback_span_tracks_multiline_and_trailing_newline_endpoints():
    assert _chunk_for("α\nbeta").span == (1, 0, 2, 4)
    assert _chunk_for("alpha\n").span == (1, 0, 2, 0)
