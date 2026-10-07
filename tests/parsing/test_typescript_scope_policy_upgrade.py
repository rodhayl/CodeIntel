"""A v5 syntax generation must rebuild even when source bytes have not changed."""
from codeintel.freshness.barrier import compute_index_build_fingerprint
from codeintel.service.production import create_default_service


def test_v5_typed_scope_policy_is_not_reused_after_repair(tmp_path, monkeypatch):
    repo, state = tmp_path / "repo", tmp_path / "state"
    repo.mkdir()
    (repo / "sample.ts").write_text(
        "class Client { go() {} }\nfunction run(obj: Client) { obj.go(); }\n"
    )
    current = compute_index_build_fingerprint()
    with monkeypatch.context() as previous_policy:
        previous_policy.setattr(
            "codeintel.freshness.barrier.INDEX_CHUNK_POLICY_VERSION",
            "utf8-entity-partition-v5-definition-scope-provenance",
        )
        previous = compute_index_build_fingerprint()
        assert previous != current
        with create_default_service(str(repo), state_dir=str(state)) as service:
            first = service.reindex()
            original_snapshot = service.barrier.active_generation.snapshot_hash
            assert service.barrier.active_generation.build_fingerprint == previous
    with create_default_service(str(repo), state_dir=str(state)) as service:
        second = service.reindex()
        assert second["status"] == "SUCCESS"
        assert second["generation_id"] != first["generation_id"]
        assert service.barrier.active_generation.snapshot_hash == original_snapshot
        assert service.barrier.active_generation.build_fingerprint == current
