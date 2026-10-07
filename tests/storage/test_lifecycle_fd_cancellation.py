from __future__ import annotations

import os

import pytest

import codeintel.storage.lifecycle as lifecycle


@pytest.mark.skipif(os.name != "posix", reason="descriptor-bound lifecycle test")
def test_open_state_dir_cancellation_closes_descriptor(tmp_path, monkeypatch):
    real_open = os.open
    real_fstat = os.fstat
    opened = []

    def tracking_open(*args, **kwargs):
        fd = real_open(*args, **kwargs)
        opened.append(fd)
        return fd

    def cancel_directory(_mode):
        raise KeyboardInterrupt("cancel state validation")

    monkeypatch.setattr(lifecycle.os, "open", tracking_open)
    monkeypatch.setattr(lifecycle.stat, "S_ISDIR", cancel_directory)

    with pytest.raises(KeyboardInterrupt, match="cancel state validation"):
        lifecycle._open_real_state_dir(tmp_path)

    assert opened
    with pytest.raises(OSError):
        real_fstat(opened[-1])


@pytest.mark.skipif(os.name != "posix", reason="descriptor-bound lifecycle test")
def test_open_rebuild_lock_cancellation_closes_descriptor(tmp_path, monkeypatch):
    real_open = os.open
    real_fstat = os.fstat
    state_fd = real_open(tmp_path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    opened = []

    def tracking_open(*args, **kwargs):
        fd = real_open(*args, **kwargs)
        opened.append(fd)
        return fd

    def cancel_regular(_mode):
        raise KeyboardInterrupt("cancel lock validation")

    monkeypatch.setattr(lifecycle.os, "open", tracking_open)
    monkeypatch.setattr(lifecycle.stat, "S_ISREG", cancel_regular)
    try:
        with pytest.raises(KeyboardInterrupt, match="cancel lock validation"):
            lifecycle._open_rebuild_lock(state_fd)
        assert opened
        with pytest.raises(OSError):
            real_fstat(opened[-1])
    finally:
        os.close(state_fd)


@pytest.mark.skipif(os.name != "posix", reason="descriptor-bound lifecycle test")
def test_open_sidecar_dir_cancellation_closes_descriptor(tmp_path, monkeypatch):
    (tmp_path / ".codeintel_vectors").mkdir()
    real_open = os.open
    real_fstat = os.fstat
    state_fd = real_open(tmp_path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    opened = []

    def tracking_open(*args, **kwargs):
        fd = real_open(*args, **kwargs)
        opened.append(fd)
        return fd

    def cancel_directory(_mode):
        raise KeyboardInterrupt("cancel sidecar validation")

    monkeypatch.setattr(lifecycle.os, "open", tracking_open)
    monkeypatch.setattr(lifecycle.stat, "S_ISDIR", cancel_directory)
    try:
        with pytest.raises(KeyboardInterrupt, match="cancel sidecar validation"):
            lifecycle._open_sidecar_dir(state_fd)
        assert opened
        with pytest.raises(OSError):
            real_fstat(opened[-1])
    finally:
        os.close(state_fd)
