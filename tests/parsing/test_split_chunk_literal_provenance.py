"""Every reported chunk span selects its exact literal UTF-8 source bytes."""
import pytest

from codeintel.core.contracts import MAX_INDEX_CHUNK_BYTES
from codeintel.core.identity import canonical_hash, make_chunk_id
from codeintel.core.models import Chunk
from codeintel.parsing.chunking import bound_chunks, split_chunk


def _chunk(text, span):
    digest = canonical_hash(text)
    try:
        cid = make_chunk_id("file_test", "gen_test", span, digest)
    except ValueError:
        cid = f"chk_{digest[:16]}"
    return Chunk(
        cid,
        "file_test", "gen_test", "src/sample.py", span, text, digest, ["ent_owner"],
    )


def _offset(raw, line, column):
    # Independent byte-offset oracle: only LF advances the repository line.
    lines = raw.split(b"\n")
    assert 1 <= line <= len(lines)
    assert 0 <= column <= len(lines[line - 1])
    return sum(len(part) + 1 for part in lines[:line - 1]) + column


@pytest.mark.parametrize("entrypoint", ["split", "bound"])
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_split_spans_select_exact_literal_source(entrypoint, newline):
    line = "value = '" + "\u03b1" * 40 + "'" + newline
    text = line * (MAX_INDEX_CHUNK_BYTES // len(line.encode()) + 5)
    source = b"\n" * 4 + b" " * 7 + text.encode()
    original = _chunk(text, (5, 7, 5 + text.count("\n"), 0))
    chunks = (
        split_chunk(original) if entrypoint == "split"
        else bound_chunks([original])
    )
    assert len(chunks) > 1
    for chunk in chunks:
        sl, sc, el, ec = chunk.span
        selected = source[_offset(source, sl, sc):_offset(source, el, ec)]
        assert selected == chunk.content.encode(), (chunk.span, len(selected), len(chunk.content.encode()))
        assert len(selected) <= MAX_INDEX_CHUNK_BYTES
        assert chunk.content_hash == canonical_hash(chunk.content)
        assert chunk.chunk_id == make_chunk_id(chunk.file_id, chunk.generation_id, chunk.span, chunk.content_hash)
        assert chunk.entity_ids == ["ent_owner"]
    assert "".join(chunk.content for chunk in chunks) == text
    assert all(previous.span[2:] == following.span[:2]
               for previous, following in zip(chunks, chunks[1:]))


def test_small_direct_split_canonicalizes_valid_but_stale_endpoint():
    original = _chunk("\u03b1\u03b2", (20, 7, 20, 7))
    result = split_chunk(original)
    assert result[0].span == (20, 7, 20, 11)
    assert result == bound_chunks([original])
    assert result[0].content_hash == canonical_hash(original.content)
    assert result[0].chunk_id != original.chunk_id


@pytest.mark.parametrize("span", [(20, 7, 20, 0), (20, 0, 19, 10)])
@pytest.mark.parametrize("entrypoint", ["split", "bound"])
def test_canonicalization_still_rejects_reversed_input_spans(span, entrypoint):
    original = _chunk("example", span)
    with pytest.raises(ValueError, match="end must not precede start"):
        if entrypoint == "split":
            split_chunk(original)
        else:
            bound_chunks([original])


def test_already_canonical_small_chunk_retains_object_identity():
    original = _chunk("alpha", (2, 3, 2, 8))
    assert split_chunk(original)[0] is original


@pytest.mark.parametrize("entrypoint", ["split", "bound"])
def test_retired_overlap_option_is_not_a_second_chunk_policy(entrypoint):
    original = _chunk("alpha", (2, 3, 2, 8))
    with pytest.raises(TypeError, match="overlap_lines"):
        if entrypoint == "split":
            split_chunk(original, overlap_lines=2)
        else:
            bound_chunks([original], overlap_lines=2)
