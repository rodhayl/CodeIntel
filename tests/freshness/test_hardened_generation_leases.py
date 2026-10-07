import hashlib
import os
from pathlib import Path

import pytest

from codeintel.freshness.hardened import HardenedProductionGenerationBarrier


def _bare_barrier(state_dir: Path):
    barrier = object.__new__(HardenedProductionGenerationBarrier)
    barrier.state_dir = str(state_dir)
    barrier._active_lease_fd = None
    barrier._leased_generation_id = None
    return barrier


def _check_symlink_privilege():
    if os.name != "posix":
        try:
            p = Path("test_symlink_probe.tmp")
            p.symlink_to("target")
            p.unlink()
        except OSError:
            pytest.skip("Windows unprivileged user cannot create symlinks")


def test_hardened_generation_lease_refuses_symlink_directory(tmp_path):
    _check_symlink_privilege()
    state = tmp_path / "state"
    outside = tmp_path / "outside"
    state.mkdir()
    outside.mkdir()
    (state / "generation_leases").symlink_to(outside, target_is_directory=True)
    barrier = _bare_barrier(state)

    with pytest.raises(RuntimeError, match="lease directory"):
        barrier._switch_generation_lease("gen_1")

    assert list(outside.iterdir()) == []
    assert barrier._active_lease_fd is None


def test_hardened_generation_lease_refuses_symlink_file(tmp_path):
    _check_symlink_privilege()
    state = tmp_path / "state"
    lease_dir = state / "generation_leases"
    state.mkdir()
    lease_dir.mkdir()
    target = tmp_path / "outside.lock"
    target.write_text("outside", encoding="utf-8")
    name = hashlib.sha256(b"gen_1").hexdigest()[:32] + ".lock"
    (lease_dir / name).symlink_to(target)
    barrier = _bare_barrier(state)

    with pytest.raises(RuntimeError, match="lease cannot be opened safely"):
        barrier._switch_generation_lease("gen_1")

    assert target.read_text(encoding="utf-8") == "outside"
    assert barrier._active_lease_fd is None


def test_hardened_generation_lease_normal_lifecycle(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    barrier = _bare_barrier(state)

    barrier._switch_generation_lease("gen_1")
    assert barrier._active_lease_fd is not None
    assert barrier._leased_generation_id == "gen_1"
    lease_path = Path(barrier._generation_lease_path("gen_1"))
    assert lease_path.is_file()
    assert not lease_path.is_symlink()
    if os.name == "posix":
        assert (os.stat(lease_path).st_mode & 0o777) == 0o600

    barrier._switch_generation_lease(None)
    assert barrier._active_lease_fd is None
    assert barrier._leased_generation_id is None
