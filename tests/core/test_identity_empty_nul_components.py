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


@pytest.mark.parametrize("bad", ["", "a\x00b"])
def test_file_id_rejects_empty_or_nul_repo_identity(bad):
    with pytest.raises(ValueError, match="repo_id.*non-empty NUL-free"):
        make_file_id(bad, "a.py")


@pytest.mark.parametrize("index", range(3))
@pytest.mark.parametrize("bad", ["", "a\x00b"])
def test_entity_id_rejects_empty_or_nul_text_components(index, bad):
    args = ["file_1", "pkg.func", "FUNCTION"]
    args[index] = bad
    with pytest.raises(ValueError, match="non-empty NUL-free"):
        make_entity_id(*args)


@pytest.mark.parametrize("index", range(4))
@pytest.mark.parametrize("bad", ["", "a\x00b"])
def test_relation_id_rejects_empty_or_nul_text_components(index, bad):
    args = ["ent_a", "CALLS", "ent_b", "file_a", SPAN]
    args[index] = bad
    with pytest.raises(ValueError, match="non-empty NUL-free"):
        make_relation_id(*args)


@pytest.mark.parametrize("bad", ["", "a\x00b"])
def test_chunk_and_evidence_ids_reject_empty_or_nul_text_components(bad):
    for args in (
        (bad, "gen_1", SPAN, "abc"),
        ("file_1", bad, SPAN, "abc"),
        ("file_1", "gen_1", SPAN, bad),
    ):
        with pytest.raises(ValueError, match="non-empty NUL-free"):
            make_chunk_id(*args)
    for args in (
        (bad, "file_1", SPAN, "abc"),
        ("gen_1", bad, SPAN, "abc"),
        ("gen_1", "file_1", SPAN, bad),
    ):
        with pytest.raises(ValueError, match="non-empty NUL-free"):
            make_evidence_id(*args)


def test_valid_identity_preimages_remain_deterministic():
    assert make_file_id("repo_1", "a.py") == make_file_id("repo_1", "a.py")
    assert make_entity_id("file_1", "pkg.func", "FUNCTION") == make_entity_id(
        "file_1", "pkg.func", "FUNCTION"
    )
