from __future__ import annotations

import pytest

from codeintel.core.identity import (
    make_chunk_id,
    make_entity_id,
    make_evidence_id,
    make_file_id,
    make_relation_id,
)

SPAN = (1, 0, 1, 1)


def test_file_id_rejects_repo_id_coercion_collision():
    canonical = make_file_id("1", "a.py")
    with pytest.raises(ValueError, match="repo_id must be text"):
        make_file_id(1, "a.py")
    assert make_file_id("1", "a.py") == canonical


def test_entity_id_rejects_text_component_coercion():
    for args in ((1, "x", "FUNCTION"), ("file_1", 1, "FUNCTION"), ("file_1", "x", 1)):
        with pytest.raises(ValueError, match="must be text"):
            make_entity_id(*args)


def test_relation_id_rejects_text_component_coercion():
    base = ["ent_a", "CALLS", "ent_b", "file_a", SPAN]
    for index in range(4):
        args = list(base)
        args[index] = 1
        with pytest.raises(ValueError, match="must be text"):
            make_relation_id(*args)


def test_chunk_and_evidence_ids_reject_text_component_coercion():
    for args in (
        (1, "gen_1", SPAN, "abc"),
        ("file_1", 1, SPAN, "abc"),
        ("file_1", "gen_1", SPAN, 1),
    ):
        with pytest.raises(ValueError, match="must be text"):
            make_chunk_id(*args)
    for args in (
        (1, "file_1", SPAN, "abc"),
        ("gen_1", 1, SPAN, "abc"),
        ("gen_1", "file_1", SPAN, 1),
    ):
        with pytest.raises(ValueError, match="must be text"):
            make_evidence_id(*args)
