from types import SimpleNamespace

import pytest

from codeintel.core.models import FileRecord
from codeintel.storage.production import ProductionSQLiteStore


def _generation(store, repo_id, sequence, marker):
    return store.create_generation(
        repo_id=repo_id,
        sequence=sequence,
        snapshot_hash=marker * 64,
        metadata={"lifecycle_state": "STAGING"},
    )


def test_production_file_batch_cannot_mix_generations_or_repos(tmp_path):
    store = ProductionSQLiteStore(str(tmp_path / "db.sqlite"))
    try:
        gen1 = _generation(store, "repo_1", 1, "a")
        gen2 = _generation(store, "repo_1", 2, "b")
        file1 = FileRecord("file_1", "repo_1", gen1.generation_id, "a.py", "h1", 1)
        file2 = FileRecord("file_2", "repo_1", gen2.generation_id, "b.py", "h2", 1)

        with pytest.raises(ValueError, match="exactly one generation and repo"):
            store.add_files([file1, file2])
        assert store.get_files(gen1.generation_id) == []
        assert store.get_files(gen2.generation_id) == []

        wrong_repo = FileRecord(
            "file_3", "repo_other", gen1.generation_id, "c.py", "h3", 1
        )
        with pytest.raises(ValueError, match="does not own generation"):
            store.add_files([wrong_repo])
        assert store.get_files(gen1.generation_id) == []
    finally:
        store.close()


def test_all_production_semantic_batch_entrypoints_enforce_generation_owner(tmp_path):
    store = ProductionSQLiteStore(str(tmp_path / "db.sqlite"))
    try:
        gen1 = _generation(store, "repo_1", 1, "a")
        wrong_scope = SimpleNamespace(
            generation_id=gen1.generation_id,
            repo_id="repo_other",
        )
        with pytest.raises(ValueError, match="does not own generation"):
            store.add_entities([wrong_scope])
        with pytest.raises(ValueError, match="does not own generation"):
            store.add_relations([wrong_scope])
    finally:
        store.close()
