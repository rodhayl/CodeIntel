import pytest
from codeintel.core.contracts import INDEX_CHUNK_POLICY_VERSION

from codeintel.freshness.barrier import compute_index_build_fingerprint
from codeintel.service.production import create_default_service
from tests.legacy_build_fingerprint import legacy_build_fingerprint


_PREVIOUS_POLICIES = ["utf8-entity-partition-v3-byte-end",
                      "utf8-entity-partition-v4-lf-syntax-provenance",
                      INDEX_CHUNK_POLICY_VERSION]


@pytest.mark.parametrize("policy", _PREVIOUS_POLICIES)
def test_repaired_syntax_policy_changes_persisted_build_identity(policy):
    previous = legacy_build_fingerprint(chunking_policy_version=policy)
    assert compute_index_build_fingerprint() != previous


@pytest.mark.parametrize("policy", _PREVIOUS_POLICIES)
def test_old_policy_generation_is_rebuilt_even_when_source_is_unchanged(tmp_path, monkeypatch, policy):
    repo, state = tmp_path / "repo", tmp_path / "state"
    repo.mkdir()
    (repo / "sample.py").write_text("def target(): return 1\n")
    previous = legacy_build_fingerprint(chunking_policy_version=policy)
    with monkeypatch.context() as legacy:
        legacy.setattr("codeintel.service.production.compute_index_build_fingerprint", lambda **kwargs: previous)
        with create_default_service(str(repo), state_dir=str(state)) as service:
            first = service.reindex()
            original_snapshot = service.barrier.active_generation.snapshot_hash
    with create_default_service(str(repo), state_dir=str(state)) as service:
        second = service.reindex()
        assert second["status"] == "SUCCESS"
        assert second["generation_id"] != first["generation_id"]
        assert service.barrier.active_generation.snapshot_hash == original_snapshot
        assert service.barrier.active_generation.build_fingerprint == compute_index_build_fingerprint()
