import asyncio
import os

import pytest

from codeintel.freshness import hardened


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX descriptors")
@pytest.mark.parametrize("error_type", [KeyboardInterrupt, asyncio.CancelledError])
@pytest.mark.parametrize("boundary", ["state_root", "lease_directory", "shared_lease"])
def test_acquisition_cancellation_closes_unowned_descriptors(tmp_path, monkeypatch, boundary, error_type):
    barrier = object.__new__(hardened.HardenedProductionGenerationBarrier)
    barrier.state_dir = str(tmp_path)
    barrier._active_lease_fd = None
    barrier._leased_generation_id = None
    real_open, real_fstat = os.open, os.fstat
    opened, calls = [], []

    def track_open(*args, **kwargs):
        fd = real_open(*args, **kwargs)
        opened.append(fd)
        return fd

    def cancel_validation(fd):
        calls.append(fd)
        if len(calls) == (1 if boundary == "state_root" else 2):
            raise error_type("descriptor acquisition interrupted")
        return real_fstat(fd)

    def cancel_flock(*_args):
        raise error_type("descriptor acquisition interrupted")

    monkeypatch.setattr(hardened.os, "open", track_open)
    if boundary == "shared_lease":
        monkeypatch.setattr(hardened.fcntl, "flock", cancel_flock)
        operation = lambda: barrier._switch_generation_lease("gen_cancelled")
    else:
        monkeypatch.setattr(hardened.os, "fstat", cancel_validation)
        operation = barrier._open_state_root_fd if boundary == "state_root" else barrier._open_lease_dir_fd
    try:
        with pytest.raises(error_type, match="descriptor acquisition interrupted"):
            operation()
        for fd in set(opened):
            with pytest.raises(OSError):
                real_fstat(fd)
        assert barrier._active_lease_fd is None
    finally:
        for fd in set(opened):
            try:
                os.close(fd)
            except OSError:
                pass
