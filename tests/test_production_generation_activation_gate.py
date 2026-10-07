import pytest

from codeintel.storage.production import ProductionSQLiteStore


def test_production_store_refuses_to_activate_staging_generation(tmp_path):
    store = ProductionSQLiteStore(str(tmp_path / "db.sqlite"))
    try:
        generation = store.create_generation(
            repo_id="repo_1",
            sequence=1,
            snapshot_hash="a" * 64,
            metadata={"lifecycle_state": "STAGING"},
        )

        with pytest.raises(RuntimeError, match="requires COMMITTED lifecycle state"):
            store.activate_generation(generation.generation_id)

        assert store.get_active_generation("repo_1") is None

        store.update_generation_metadata(
            generation.generation_id,
            {"lifecycle_state": "COMMITTED"},
        )
        store.activate_generation(generation.generation_id)
        active = store.get_active_generation("repo_1")
        assert active is not None
        assert active.generation_id == generation.generation_id
    finally:
        store.close()


def test_failed_activation_does_not_deactivate_existing_committed_generation(tmp_path):
    store = ProductionSQLiteStore(str(tmp_path / "db.sqlite"))
    try:
        first = store.create_generation(
            repo_id="repo_1",
            sequence=1,
            snapshot_hash="a" * 64,
            metadata={"lifecycle_state": "COMMITTED"},
        )
        store.activate_generation(first.generation_id)

        staging = store.create_generation(
            repo_id="repo_1",
            sequence=2,
            snapshot_hash="b" * 64,
            metadata={"lifecycle_state": "STAGING"},
        )
        with pytest.raises(RuntimeError, match="requires COMMITTED lifecycle state"):
            store.activate_generation(staging.generation_id)

        active = store.get_active_generation("repo_1")
        assert active is not None
        assert active.generation_id == first.generation_id
    finally:
        store.close()
