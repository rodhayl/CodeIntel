from __future__ import annotations

import pytest

from codeintel.core.identity import make_chunk_id, make_evidence_id, make_relation_id

MAKERS = (
    lambda span: make_relation_id("ent_a", "CALLS", "ent_b", "file_a", span),
    lambda span: make_chunk_id("file_a", "gen_1", span, "abc"),
    lambda span: make_evidence_id("gen_1", "file_a", span, "abc"),
)


def test_span_coordinates_are_type_strict_without_changing_valid_preimage():
    for make in MAKERS:
        canonical = make((1, 0, 1, 2))
        assert make([1, 0, 1, 2]) == canonical
        for bad in (("1", "0", "1", "2"), (True, 0, 1, 2), (1, 0, 1, 2.0)):
            with pytest.raises(ValueError, match="coordinates must be integers"):
                make(bad)


def test_span_requires_exact_shape():
    for make in MAKERS:
        for bad in ((), (1,), (1, 0, 1), (1, 0, 1, 2, 3), None):
            with pytest.raises(ValueError, match="exactly four"):
                make(bad)


def test_span_rejects_coordinates_outside_canonical_source_domain():
    for make in MAKERS:
        for bad in ((0, 0, 1, 0), (1, -1, 1, 0), (1, 0, 0, 0), (1, 0, 1, -1)):
            with pytest.raises(ValueError, match="canonical source domain"):
                make(bad)


def test_span_rejects_reversed_ranges():
    for make in MAKERS:
        for bad in ((2, 0, 1, 0), (1, 5, 1, 4)):
            with pytest.raises(ValueError, match="end precedes"):
                make(bad)
