from __future__ import annotations

import pytest

from codeintel.core.identity import canonical_hash
from codeintel.core.models import Chunk
from codeintel.retrieval.hybrid import HybridRetriever


class _Lexical:
    def __init__(self, chunk: Chunk):
        self.chunk = chunk

    def search(self, query, limit=20, generation_id=None):
        return [(self.chunk, 1.0)]


class _Graph:
    pass


def _chunk(*, content="print('ok')\n", content_hash=None):
    if content_hash is None and isinstance(content, str):
        content_hash = canonical_hash(content)
    return Chunk(
        chunk_id="chk_fixture",
        file_id="file_fixture",
        generation_id="gen_fixture",
        rel_path="src/a.py",
        span=(1, 0, 2, 0),
        content=content,
        content_hash=content_hash,
        entity_ids=[],
    )


def _retrieve(chunk: Chunk):
    retriever = HybridRetriever(_Graph(), _Lexical(chunk))
    return retriever.retrieve(
        "print",
        "gen_fixture",
        limit=1,
        expand_graph=False,
        enable_exact_entity=False,
    )


def test_hybrid_retrieval_rejects_stale_literal_content_hash():
    chunk = _chunk(content_hash=canonical_hash("old bytes\n"))
    with pytest.raises(RuntimeError, match="mismatched content hash"):
        _retrieve(chunk)


@pytest.mark.parametrize(
    "chunk",
    [
        _chunk(content=b"not text", content_hash="hash"),
        _chunk(content_hash=7),
    ],
)
def test_hybrid_retrieval_rejects_malformed_literal_provenance(chunk):
    with pytest.raises(RuntimeError, match="invalid literal chunk provenance"):
        _retrieve(chunk)


def test_hybrid_retrieval_preserves_bound_literal_source():
    chunk = _chunk()
    output = _retrieve(chunk)
    assert len(output) == 1
    assert output[0].source_text == chunk.content
    assert output[0].content_hash == canonical_hash(chunk.content)
    assert output[0].path == "src/a.py"
