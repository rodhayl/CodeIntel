from __future__ import annotations

import os

import pytest

import codeintel.core.security as security


def _paths(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    return repo, tmp_path / "state"


def test_state_layout_marker_handles_short_writes(tmp_path, monkeypatch):
    repo, state = _paths(tmp_path)
    real_write = os.write
    writes = []

    def short_write(fd, data):
        payload = bytes(data[:1])
        writes.append(payload)
        return real_write(fd, payload)

    monkeypatch.setattr(security.os, "write", short_write)
    assert security.validate_trusted_state_dir(str(repo), str(state)) == str(state)
    assert (state / security.STATE_LAYOUT_MARKER).read_bytes() == b"2\n"
    assert len(writes) == 2


def test_failed_initial_marker_write_does_not_poison_state_dir(tmp_path, monkeypatch):
    repo, state = _paths(tmp_path)
    real_write = os.write
    calls = 0

    def fail_after_partial(fd, data):
        nonlocal calls
        calls += 1
        if calls == 1:
            return real_write(fd, bytes(data[:1]))
        raise OSError("simulated ENOSPC")

    monkeypatch.setattr(security.os, "write", fail_after_partial)
    with pytest.raises(OSError, match="simulated ENOSPC"):
        security.validate_trusted_state_dir(str(repo), str(state))
    assert not (state / security.STATE_LAYOUT_MARKER).exists()

    monkeypatch.setattr(security.os, "write", real_write)
    assert security.validate_trusted_state_dir(str(repo), str(state)) == str(state)
    assert (state / security.STATE_LAYOUT_MARKER).read_bytes() == b"2\n"


def test_cancelled_initial_marker_write_does_not_poison_state_dir(tmp_path, monkeypatch):
    repo, state = _paths(tmp_path)
    real_write = os.write

    def cancel_write(fd, data):
        real_write(fd, bytes(data[:1]))
        raise KeyboardInterrupt("cancel marker init")

    monkeypatch.setattr(security.os, "write", cancel_write)
    with pytest.raises(KeyboardInterrupt, match="cancel marker init"):
        security.validate_trusted_state_dir(str(repo), str(state))
    assert not (state / security.STATE_LAYOUT_MARKER).exists()
