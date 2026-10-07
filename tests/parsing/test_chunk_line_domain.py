from __future__ import annotations

import pytest

from codeintel.core.identity import canonical_hash
from codeintel.core.models import Chunk
from codeintel.parsing.chunking import split_chunk


def _raw_chunk(content: str, span) -> Chunk:
    return Chunk(
        chunk_id="chk_direct_fixture",
        file_id="file_test",
        generation_id="gen_test",
        rel_path="src/example.py",
        span=span,
        content=content,
        content_hash=canonical_hash(content),
        entity_ids=[],
    )


@pytest.mark.parametrize(
    "content,span",
    [
        ("", (0, 0, 0, 0)),
        ("abc", (0, 0, 0, 3)),
        ("α", (0, 2, 0, 4)),
    ],
)
def test_split_chunk_rejects_content_consistent_line_zero_provenance(content, span):
    with pytest.raises(ValueError, match="canonical source domain"):
        split_chunk(_raw_chunk(content, span))


def test_split_chunk_preserves_valid_already_canonical_one_based_span():
    chunk = _raw_chunk("abc", (1, 2, 1, 5))
    assert split_chunk(chunk) == [chunk]
