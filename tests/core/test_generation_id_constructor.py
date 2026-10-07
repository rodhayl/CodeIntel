"""Input validation belongs to the active storage constructor, not a retired helper."""
import hashlib
import json

import pytest

from codeintel.storage.production import ProductionSQLiteStore


def test_generation_id_rejects_sequence_values_that_can_collide_by_coercion(tmp_path):
    with ProductionSQLiteStore(str(tmp_path / "db.sqlite")) as store:
        for bad in (True, False, 0, -1, 1.0, "1", None):
            with pytest.raises(ValueError, match="positive integer"):
                store.create_generation("repo", bad, "a" * 64)


def test_generation_id_requires_supported_lowercase_snapshot_identity(tmp_path):
    with ProductionSQLiteStore(str(tmp_path / "db.sqlite")) as store:
        for bad in ("abc", "A" * 64, "g" * 64, "a" * 63, "a" * 65, None, 123):
            with pytest.raises(ValueError, match="lowercase SHA-256"):
                store.create_generation("repo", 1, bad)


def test_generation_id_uses_full_repo_sequence_snapshot_identity(tmp_path):
    with ProductionSQLiteStore(str(tmp_path / "db.sqlite")) as store:
        for sequence in (1, 1000000):
            snapshot = "0123456789abcdef" * 4
            payload = json.dumps(["codeintel-generation-v2", "repo", sequence, snapshot], ensure_ascii=True, separators=(",", ":"))
            expected = f"gen_{sequence:06d}_" + hashlib.sha256(payload.encode()).hexdigest()
            assert store.create_generation("repo", sequence, snapshot).generation_id == expected
