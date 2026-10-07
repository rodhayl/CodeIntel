from pathlib import Path

from codeintel.storage.lifecycle import cleanup_orphan_vector_artifacts


def test_cleanup_refuses_symlinked_rebuild_lock_without_touching_target(tmp_path: Path):
    state = tmp_path / "state"
    state.mkdir()
    external = tmp_path / "external.txt"
    external.write_text("outside", encoding="utf-8")
    (state / "rebuild.lock").symlink_to(external)

    report = cleanup_orphan_vector_artifacts(str(state), [])

    assert report["unsafe_state_dir"] is True
    assert any(str(item).startswith("rebuild_lock:unsafe:") for item in report["errors"])
    assert external.read_text(encoding="utf-8") == "outside"
    assert (state / "rebuild.lock").is_symlink()
