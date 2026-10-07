"""Post-create metadata failures must not strand a live service's rebuild lock."""
import asyncio

import pytest

from codeintel.service import create_default_service


@pytest.mark.parametrize("error_type", [RuntimeError, KeyboardInterrupt, asyncio.CancelledError])
def test_staging_metadata_failure_aborts_and_preserves_old_active(tmp_path, monkeypatch, error_type):
    repo = tmp_path / "repo"
    repo.mkdir()
    source = repo / "a.py"
    source.write_text("def original():\n    return 1\n", encoding="utf-8")
    with create_default_service(str(repo), state_dir=str(tmp_path / "state")) as service:
        service.reindex()
        old = service.barrier.active_generation.generation_id
        source.write_text("def changed():\n    return 2\n", encoding="utf-8")
        monkeypatch.setattr(service.barrier, "_active_artifact_repair_reason", lambda: "injected repair")
        original_update = service.generation_store.update_generation_metadata

        def fail_update(generation_id, metadata):
            if "artifact_repair_required" in metadata:
                raise error_type("injected staging metadata failure")
            return original_update(generation_id, metadata)

        monkeypatch.setattr(service.generation_store, "update_generation_metadata", fail_update)
        with pytest.raises(error_type, match="injected staging metadata failure"):
            service.reindex()
        assert service.barrier._rebuild_lock_file is None
        assert service.barrier.is_rebuilding is False
        assert service.barrier.active_generation.generation_id == old
        assert [g.generation_id for g in service.generation_store.list_generations(service.repo_id)] == [old]

        monkeypatch.setattr(service.generation_store, "update_generation_metadata", original_update)
        assert service.reindex()["status"] == "SUCCESS"
        assert service.barrier.active_generation.generation_id != old


def test_staging_cleanup_failure_does_not_replace_metadata_error(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    source = repo / "a.py"
    source.write_text("value = 1\n", encoding="utf-8")
    with create_default_service(str(repo), state_dir=str(tmp_path / "state")) as service:
        service.reindex()
        old = service.barrier.active_generation.generation_id
        source.write_text("value = 2\n", encoding="utf-8")
        monkeypatch.setattr(service.barrier, "_active_artifact_repair_reason", lambda: "injected repair")
        original_abort = service.barrier.abort_staging_generation

        def fail_update(_generation_id, _metadata):
            raise KeyboardInterrupt("primary metadata cancellation")

        def abort_then_fail(generation):
            original_abort(generation)
            raise RuntimeError("secondary cleanup failure")

        monkeypatch.setattr(service.generation_store, "update_generation_metadata", fail_update)
        monkeypatch.setattr(service.barrier, "abort_staging_generation", abort_then_fail)
        with pytest.raises(KeyboardInterrupt, match="primary metadata cancellation") as caught:
            service.reindex()
        assert any("secondary cleanup failure" in note for note in caught.value.__notes__)
        assert service.barrier._rebuild_lock_file is None
        assert service.barrier.active_generation.generation_id == old
