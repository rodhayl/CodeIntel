"""Staging records the only maintained indexing mode directly."""
from codeintel.service import create_default_service


def test_changed_and_unchanged_snapshots_use_full_rebuild_or_reuse(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    source = repo / "a.py"
    source.write_text("value = 1\n")
    with create_default_service(str(repo), str(tmp_path / "state")) as service:
        first = service.reindex()
        assert service.reindex()["generation_id"] == first["generation_id"]
        source.write_text("value = 2\n")
        staging = service.barrier.create_staging_generation()
        try:
            assert staging.generation.generation_id != first["generation_id"]
            assert staging.generation.metadata["indexing_mode"] == "FULL_REBUILD_V1"
            assert not hasattr(staging, "prior_generation")
            assert not hasattr(staging, "prior_hashes")
            assert "production_incremental_reuse" not in staging.generation.metadata
        finally:
            service.barrier.abort_staging_generation(staging.generation)
