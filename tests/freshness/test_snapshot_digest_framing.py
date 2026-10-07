"""Snapshot identity frames paths and hashes instead of ambiguous delimiters."""
import hashlib
import os

import pytest

from codeintel.freshness.barrier import GenerationBarrier, compute_index_build_fingerprint
from tests.legacy_build_fingerprint import legacy_build_fingerprint
from codeintel.lab.retrieval import index_repository, query_repository, verify_packet


FIRST = b"def alpha(): return 1\n"
SECOND = b"def beta(): return 2\n"
FIRST_HASH = hashlib.sha256(FIRST).hexdigest()
SECOND_HASH = hashlib.sha256(SECOND).hexdigest()
COMBINED_PATH = "a.py:" + FIRST_HASH + "\nb.py"


def legacy_hash(hashes):
    return hashlib.sha256("\n".join(sorted(f"{k}:{v}" for k, v in hashes.items())).encode()).hexdigest()


def test_delimiter_ambiguity_cannot_collapse_distinct_path_maps():
    two_files = {"a.py": FIRST_HASH, "b.py": SECOND_HASH}
    one_file = {COMBINED_PATH: SECOND_HASH}
    assert legacy_hash(two_files) == legacy_hash(one_file)
    assert GenerationBarrier._snapshot_hash(two_files) != GenerationBarrier._snapshot_hash(one_file)


def test_framing_is_order_independent_but_preserves_exact_unicode_and_separators():
    hashes = {"line\nbreak.py": FIRST_HASH, "colon:name.py": SECOND_HASH,
              "caf\u00e9.py": FIRST_HASH, "cafe\u0301.py": SECOND_HASH}
    assert GenerationBarrier._snapshot_hash(hashes) == GenerationBarrier._snapshot_hash(dict(reversed(list(hashes.items()))))
    assert GenerationBarrier._snapshot_hash({"caf\u00e9.py": FIRST_HASH}) != GenerationBarrier._snapshot_hash({"cafe\u0301.py": FIRST_HASH})
    assert GenerationBarrier._snapshot_hash({"line\nbreak.py": FIRST_HASH}) != GenerationBarrier._snapshot_hash({"line:break.py": FIRST_HASH})


@pytest.mark.skipif(os.name != "posix", reason="newline/colon physical filenames")
@pytest.mark.parametrize("query", ["beta", "not_found"])
def test_ambiguous_legacy_filename_replacement_triggers_refresh(tmp_path, query):
    repo, state = tmp_path / "repo", tmp_path / "state"
    repo.mkdir()
    (repo / "a.py").write_bytes(FIRST)
    (repo / "b.py").write_bytes(SECOND)
    indexed, _ = index_repository(repo, state)
    (repo / "a.py").unlink()
    (repo / "b.py").unlink()
    (repo / COMBINED_PATH).write_bytes(SECOND)
    packet, _ = query_repository(repo, state, query)
    assert packet["refreshed"] is True
    assert packet["snapshot_sha256"] != indexed["snapshot_sha256"]
    verify_packet(repo, packet)
    if query == "beta":
        assert packet["selected"][0]["path"] == COMBINED_PATH
    else:
        assert packet["status"] == "EMPTY"


def test_prior_unframed_generation_is_rebuilt_without_source_edits(tmp_path, monkeypatch):
    repo, state = tmp_path / "repo", tmp_path / "state"
    repo.mkdir()
    (repo / "a.py").write_bytes(FIRST)
    previous = legacy_build_fingerprint(snapshot_format_version=None)
    assert previous != compute_index_build_fingerprint()
    with monkeypatch.context() as old:
        old.setattr(GenerationBarrier, "_snapshot_hash", staticmethod(legacy_hash))
        old.setattr("codeintel.service.production.compute_index_build_fingerprint", lambda **kwargs: previous)
        indexed, _ = index_repository(repo, state)
    packet, _ = query_repository(repo, state, "alpha")
    assert packet["refreshed"] is True
    assert packet["snapshot_sha256"] != indexed["snapshot_sha256"]
    assert (repo / "a.py").read_bytes() == FIRST
    verify_packet(repo, packet)
