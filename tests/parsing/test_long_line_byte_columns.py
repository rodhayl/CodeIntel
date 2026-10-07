"""Long-line segmentation has literal byte columns and bounded prefix work."""
import pytest

from codeintel.core.identity import canonical_hash, make_chunk_id
from codeintel.core.models import Chunk
from codeintel.parsing.chunking import _line_pieces, split_chunk


@pytest.mark.parametrize("unit", ["a", "é", "🙂λ"])
@pytest.mark.parametrize("ending", ["", "\n", "\r\n"])
def test_long_line_columns_select_exact_utf8_bytes(unit, ending):
    text = unit * 4096 + ending
    span = (5, 7, 5, 7)
    digest = canonical_hash(text)
    original = Chunk(make_chunk_id("file", "gen", span, digest), "file", "gen", "sample.py",
                     span, text, digest, ["ent_owner"])
    chunks = split_chunk(original, max_bytes=128)
    assert len(chunks) > 1
    assert "".join(chunk.content for chunk in chunks) == text
    raw = b"\n" * 4 + b" " * 7 + text.encode("utf-8")
    starts = [0] + [index + 1 for index, value in enumerate(raw) if value == 10]
    for chunk in chunks:
        sl, sc, el, ec = chunk.span
        selected = raw[starts[sl - 1] + sc:starts[el - 1] + ec]
        assert selected == chunk.content.encode("utf-8")
        assert len(selected) <= 128
        assert chunk.chunk_id == make_chunk_id("file", "gen", chunk.span, chunk.content_hash)


def test_long_line_does_not_reencode_accumulated_prefixes():
    class CountedText(str):
        encoded_source_characters = 0

        def split(self, separator):
            return [type(self)(part) for part in super().split(separator)]

        def __getitem__(self, key):
            result = super().__getitem__(key)
            return type(self)(result) if isinstance(key, slice) else result

        def encode(self, *args, **kwargs):
            type(self).encoded_source_characters += len(self)
            return super().encode(*args, **kwargs)

    source = CountedText("a" * (128 * 64))
    pieces = _line_pieces(source, 1, 0, max_bytes=128)
    assert "".join(piece[0] for piece in pieces) == source
    # Permit a few complete scans; forbid the sum of every preceding prefix.
    assert CountedText.encoded_source_characters <= 4 * len(source)
