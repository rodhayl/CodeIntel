import os
from pathlib import Path

import pytest

from codeintel.freshness.hardened import HardenedProductionGenerationBarrier


def _barrier_for_state(state: Path):
    barrier = object.__new__(HardenedProductionGenerationBarrier)
    barrier.state_dir = str(state)
    return barrier


def test_generation_sidecar_remove_uses_opened_directory_after_path_swap(monkeypatch, tmp_path: Path):
    state = tmp_path / "state"
    sidecars = state / ".codeintel_vectors"
    attacker = state / "attacker"
    sidecars.mkdir(parents=True)
    attacker.mkdir()
    target = "vectors_gen_1.npz"
    (sidecars / target).write_bytes(b"original")
    (attacker / target).write_bytes(b"attacker-must-survive")

    original_stat = os.stat
    swapped = {"done": False}

    def stat_then_swap(path, *args, **kwargs):
        result = original_stat(path, *args, **kwargs)
        if path == target and kwargs.get("dir_fd") is not None and not swapped["done"]:
            os.rename(sidecars, state / ".codeintel_vectors_original")
            os.rename(attacker, sidecars)
            swapped["done"] = True
        return result

    monkeypatch.setattr(os, "stat", stat_then_swap)
    _barrier_for_state(state)._remove_generation_sidecar("gen_1")

    assert swapped["done"] is True
    assert not (state / ".codeintel_vectors_original" / target).exists()
    assert (sidecars / target).read_bytes() == b"attacker-must-survive"


def test_generation_sidecar_remove_refuses_symlink_target(tmp_path: Path):
    state = tmp_path / "state"
    sidecars = state / ".codeintel_vectors"
    sidecars.mkdir(parents=True)
    external = tmp_path / "external.npz"
    external.write_bytes(b"external")
    os.symlink(external, sidecars / "vectors_gen_1.npz")

    with pytest.raises(RuntimeError, match="must be a regular file"):
        _barrier_for_state(state)._remove_generation_sidecar("gen_1")

    assert external.read_bytes() == b"external"
    assert os.path.islink(sidecars / "vectors_gen_1.npz")


def test_generation_sidecar_remove_rejects_unsafe_generation_id(tmp_path: Path):
    state = tmp_path / "state"
    state.mkdir()
    with pytest.raises(ValueError, match="unsafe sidecar characters"):
        _barrier_for_state(state)._remove_generation_sidecar("../escape")
