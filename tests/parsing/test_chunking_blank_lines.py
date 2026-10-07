import pytest

from codeintel.core.identity import canonical_hash
from codeintel.core.models import Chunk
from codeintel.parsing.chunking import split_chunk


def _chunk(content: str) -> Chunk:
    lines = content.splitlines()
    return Chunk(
        chunk_id="original",
        file_id="file_1",
        generation_id="gen_1",
        rel_path="src/a.py",
        span=(1, 0, max(1, len(lines)), len(lines[-1].encode("utf-8")) if lines else 0),
        content=content,
        content_hash=canonical_hash(content),
        entity_ids=[],
    )


@pytest.mark.parametrize(
    "content",
    [
        "a" * 126 + "\n\n" + "b" * 126,
        "a" * 124 + "\r\n\r\n" + "b" * 124,
    ],
)
def test_oversized_chunk_preserves_blank_line_bytes_exactly(content):
    pieces = split_chunk(_chunk(content), max_bytes=128)

    assert len(pieces) >= 2
    assert "".join(piece.content for piece in pieces) == content
    assert any("\n\n" in piece.content or "\r\n\r\n" in piece.content for piece in pieces)
    assert all(len(piece.content.encode("utf-8")) <= 128 for piece in pieces)
