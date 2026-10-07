from codeintel.core.identity import canonical_hash, make_chunk_id
from codeintel.core.models import Chunk
from codeintel.parsing.chunking import bound_chunks


def _chunk(content: str, span):
    content_hash = canonical_hash(content)
    return Chunk(
        chunk_id=make_chunk_id("file_test", "gen_test", span, content_hash),
        file_id="file_test",
        generation_id="gen_test",
        rel_path="src/example.py",
        span=span,
        content=content,
        content_hash=content_hash,
        entity_ids=["ent_module"],
    )


def test_bound_chunks_moves_trailing_newline_endpoint_to_next_line():
    content = "α = 1\nβ = 2\n"
    original = _chunk(content, (1, 0, 2, 0))

    result = bound_chunks([original])

    assert len(result) == 1
    chunk = result[0]
    assert chunk.content == content
    assert chunk.span == (1, 0, 3, 0)
    assert chunk.content_hash == canonical_hash(content)
    assert chunk.chunk_id == make_chunk_id(
        "file_test", "gen_test", chunk.span, chunk.content_hash
    )
    assert chunk.chunk_id != original.chunk_id


def test_bound_chunks_derives_multibyte_last_line_end_column_without_trailing_newline():
    content = "first\nβγ"
    original = _chunk(content, (5, 4, 6, 0))

    chunk = bound_chunks([original])[0]

    assert chunk.span == (5, 4, 6, len("βγ".encode("utf-8")))
    assert chunk.content == content


def test_bound_chunks_preserves_start_column_for_single_line_partial_source():
    content = "αβ"
    # A stale endpoint can be canonicalized; a reversed interval is invalid.
    original = _chunk(content, (20, 7, 20, 7))

    chunk = bound_chunks([original])[0]

    assert chunk.span == (20, 7, 20, 7 + len(content.encode("utf-8")))


def test_bound_chunks_leaves_already_canonical_chunk_identity_unchanged():
    content = "x = 1"
    span = (3, 2, 3, 2 + len(content.encode("utf-8")))
    original = _chunk(content, span)

    result = bound_chunks([original])

    assert result == [original]
