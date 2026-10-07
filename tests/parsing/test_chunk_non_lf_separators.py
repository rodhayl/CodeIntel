"""Tree-sitter source rows advance only at LF, not Python splitlines separators."""
import pytest
from codeintel.core.identity import canonical_hash, make_chunk_id
from codeintel.core.models import Chunk
from codeintel.lab.retrieval import literal_span
from codeintel.parsing.chunking import split_chunk

@pytest.mark.parametrize('separator', ['\v', '\f', '\u0085', '\u2028', '\u2029', '\r', '\r\n', '\n'])
def test_split_chunks_keep_exact_byte_rows_for_all_text_separators(separator):
    content = 'a' * 90 + separator + 'b' * 90
    raw = content.encode('utf-8')
    span = (1, 0, raw.count(b'\n') + 1, len(raw.rsplit(b'\n', 1)[-1]))
    chunk = Chunk(chunk_id=make_chunk_id('file', 'generation', span, canonical_hash(content)),
                  file_id='file', generation_id='generation', rel_path='sample.py',
                  span=span, content=content, content_hash=canonical_hash(content), entity_ids=[])
    pieces = split_chunk(chunk, max_bytes=128)
    assert ''.join(piece.content for piece in pieces) == content
    for piece in pieces:
        assert len(piece.content.encode('utf-8')) <= 128
        assert literal_span(raw, piece.span) == piece.content.encode('utf-8')
