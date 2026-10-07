from pathlib import Path

import pytest

from codeintel.freshness.hardened import HardenedProductionGenerationBarrier


def _bare_barrier(state_dir: Path):
    barrier = object.__new__(HardenedProductionGenerationBarrier)
    barrier.state_dir = str(state_dir)
    barrier._rebuild_lock_file = None
    return barrier


import os

def _check_symlink_privilege():
    if os.name != "posix":
        try:
            p = Path("test_symlink_probe2.tmp")
            p.symlink_to("target")
            p.unlink()
        except OSError:
            pytest.skip("Windows unprivileged user cannot create symlinks")


def test_hardened_rebuild_lock_refuses_symlink_without_touching_target(tmp_path):
    _check_symlink_privilege()
    state = tmp_path / "state"
    state.mkdir()
    target = tmp_path / "outside.lock"
    target.write_text("outside", encoding="utf-8")
    (state / "rebuild.lock").symlink_to(target)
    barrier = _bare_barrier(state)

    with pytest.raises(RuntimeError, match="cannot be opened safely"):
        barrier.acquire_rebuild_lock()

    assert target.read_text(encoding="utf-8") == "outside"
    assert barrier._rebuild_lock_file is None


def test_hardened_rebuild_lock_uses_regular_private_file(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    barrier = _bare_barrier(state)

    barrier.acquire_rebuild_lock()
    try:
        lock = state / "rebuild.lock"
        assert lock.is_file()
        assert not lock.is_symlink()
        assert barrier._rebuild_lock_file is not None
    finally:
        barrier.release_rebuild_lock()
