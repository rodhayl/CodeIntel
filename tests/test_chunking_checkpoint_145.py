import pytest

from codeintel.core.identity import canonical_hash
from codeintel.core.models import Chunk
from codeintel.parsing.chunking import bound_chunks, split_chunk


def _chunk(content: str, *, chunk_id: str = "original", span=None, entity_ids=None):
    if span is None:
        raw = content.encode("utf-8")
        nls = raw.count(b"\n")
        trailing = len(raw.rsplit(b"\n", 1)[-1])
        span = (10, 0, 10 + nls, trailing)
    return Chunk(
        chunk_id=chunk_id,
        file_id="file_1",
        generation_id="gen_1",
        rel_path="src/example.py",
        span=span,
        content=content,
        content_hash=canonical_hash(content),
        entity_ids=list(entity_ids or []),
    )


def test_exact_limit_line_plus_newline_never_exceeds_byte_contract():
    raw = ("x" * 1800) + "\n"
    parts = split_chunk(_chunk(raw), max_bytes=1800)
    assert "".join(part.content for part in parts) == raw
    assert all(len(part.content.encode("utf-8")) <= 1800 for part in parts)
    assert len(parts) == 2


def test_multibyte_crlf_split_reconstructs_source_exactly_and_preserves_byte_columns():
    raw = ("é" * 100) + "\r\n" + ("🙂" * 80) + "\r\n"
    parts = split_chunk(_chunk(raw, span=(7, 3, 8, 320)), max_bytes=128)
    assert "".join(part.content for part in parts) == raw
    assert all(len(part.content.encode("utf-8")) <= 128 for part in parts)
    assert parts[0].span[0] == 7
    assert parts[0].span[1] == 3
    assert all(part.span[0] <= part.span[2] for part in parts)


@pytest.mark.parametrize("bad", [127, 0, -1, True, 128.5])
def test_max_bytes_contract_rejects_invalid_values(bad):
    with pytest.raises(ValueError, match="max_bytes"):
        split_chunk(_chunk("x" * 300), max_bytes=bad)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "span,match",
    [
        ((3, 0, 2, 0), "end precedes"),
        ((3, 5, 3, 4), "end precedes"),
        ((-1, 0, 3, 0), "non-negative"),
        ((1, True, 3, 0), "non-negative"),
    ],
)
def test_common_chunk_boundary_rejects_malformed_spans(span, match):
    with pytest.raises(ValueError, match=match):
        split_chunk(_chunk("abc", span=span), max_bytes=128)


def test_conflicting_duplicate_chunk_id_fails_closed_instead_of_silent_dedupe():
    first = _chunk("alpha", chunk_id="same")
    conflicting = _chunk("beta", chunk_id="same")
    with pytest.raises(ValueError, match="Conflicting duplicate chunk_id"):
        bound_chunks([first, conflicting], max_bytes=128)


def test_exact_duplicate_chunk_id_is_deduplicated_deterministically():
    first = _chunk("alpha", chunk_id="same", entity_ids=["e1"])
    duplicate = _chunk("alpha", chunk_id="same", entity_ids=["e1"])
    result = bound_chunks([first, duplicate], max_bytes=128)
    assert result == [first]


def test_conflicting_entity_attachment_under_same_identity_is_not_silently_lost():
    first = _chunk("alpha", chunk_id="same", entity_ids=["e1"])
    conflicting = _chunk("alpha", chunk_id="same", entity_ids=["e2"])
    with pytest.raises(ValueError, match="Conflicting duplicate chunk_id"):
        bound_chunks([first, conflicting], max_bytes=128)
