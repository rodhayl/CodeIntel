import math

import pytest

from codeintel.storage.production import ProductionSQLiteStore


def test_production_generation_identity_rejects_coercible_sequence_and_bad_snapshot(tmp_path):
    store = ProductionSQLiteStore(str(tmp_path / "db.sqlite"))
    try:
        for bad_sequence in (True, False, 0, -1, 1.5, "1"):
            with pytest.raises(ValueError, match="sequence"):
                store.create_generation(
                    repo_id="repo_1",
                    sequence=bad_sequence,
                    snapshot_hash="a" * 64,
                )
        for bad_hash in ("abc", "A" * 64, "g" * 64, None):
            with pytest.raises(ValueError, match="snapshot_hash"):
                store.create_generation(
                    repo_id="repo_1",
                    sequence=1,
                    snapshot_hash=bad_hash,
                )
    finally:
        store.close()


def test_production_generation_metadata_is_strict_json_and_bounded(tmp_path):
    store = ProductionSQLiteStore(str(tmp_path / "db.sqlite"))
    try:
        for bad in (
            {"x": math.nan},
            {"x": math.inf},
            {"x": object()},
            ["not", "an", "object"],
        ):
            with pytest.raises(ValueError, match="generation metadata"):
                store.create_generation(
                    repo_id="repo_1",
                    sequence=1,
                    snapshot_hash="a" * 64,
                    metadata=bad,
                )

        generation = store.create_generation(
            repo_id="repo_1",
            sequence=1,
            snapshot_hash="a" * 64,
            metadata={"lifecycle_state": "STAGING"},
        )
        with pytest.raises(ValueError, match="strict JSON"):
            store.update_generation_metadata(
                generation.generation_id,
                {"bad": math.nan},
            )
        with pytest.raises(ValueError, match="does not exist"):
            store.update_generation_metadata(
                "gen_999999_aaaaaaaaaaaa",
                {"lifecycle_state": "COMMITTED"},
            )

        store.update_generation_metadata(
            generation.generation_id,
            {"lifecycle_state": "COMMITTED", "semantic_status": "SYNTAX_ONLY"},
        )
        row = store._execute(
            "SELECT metadata_json FROM generations WHERE generation_id = ?",
            (generation.generation_id,),
        ).fetchone()
        assert '"lifecycle_state":"COMMITTED"' in row["metadata_json"]
        assert "NaN" not in row["metadata_json"]
    finally:
        store.close()


def test_production_activation_fails_closed_on_non_strict_stored_metadata(tmp_path):
    store = ProductionSQLiteStore(str(tmp_path / "db.sqlite"))
    try:
        generation = store.create_generation(
            repo_id="repo_1",
            sequence=1,
            snapshot_hash="a" * 64,
            metadata={"lifecycle_state": "STAGING"},
        )
        # Simulate legacy/corrupt state that bypassed the production writer.
        store._execute(
            "UPDATE generations SET metadata_json = ? WHERE generation_id = ?",
            ('{"lifecycle_state":"COMMITTED","bad":NaN}', generation.generation_id),
        )
        with pytest.raises(RuntimeError, match="not strict JSON"):
            store.activate_generation(generation.generation_id)
        assert store.get_active_generation("repo_1") is None
    finally:
        store.close()
