from __future__ import annotations

import os

import pytest

import codeintel.safe_artifacts as safe_artifacts


@pytest.mark.skipif(os.name != "posix", reason="descriptor-bound POSIX artifact reader")
def test_parent_walk_cancellation_closes_current_descriptor(tmp_path, monkeypatch):
    parent = tmp_path / "nested"
    parent.mkdir()
    target = parent / "receipt.json"
    real_open = os.open
    real_fstat = os.fstat
    opened = []

    def cancel_on_nested(path, flags, *args, **kwargs):
        if path == "nested" and kwargs.get("dir_fd") is not None:
            raise KeyboardInterrupt("cancel parent walk")
        fd = real_open(path, flags, *args, **kwargs)
        opened.append(fd)
        return fd

    monkeypatch.setattr(safe_artifacts.os, "open", cancel_on_nested)
    with pytest.raises(KeyboardInterrupt, match="cancel parent walk"):
        safe_artifacts._open_parent_directory(target, label="receipt")

    assert opened
    for fd in set(opened):
        with pytest.raises(OSError):
            real_fstat(fd)


@pytest.mark.skipif(os.name != "posix", reason="descriptor-bound POSIX artifact reader")
def test_regular_validation_cancellation_closes_file_descriptor(tmp_path, monkeypatch):
    target = tmp_path / "receipt.json"
    target.write_text("{}", encoding="utf-8")
    real_open = os.open
    real_fstat = os.fstat
    file_fd = None

    def track_file_open(path, flags, *args, **kwargs):
        nonlocal file_fd
        fd = real_open(path, flags, *args, **kwargs)
        if path == target.name and kwargs.get("dir_fd") is not None:
            file_fd = fd
        return fd

    def cancel_is_regular(mode):
        raise KeyboardInterrupt("cancel regular validation")

    monkeypatch.setattr(safe_artifacts.os, "open", track_file_open)
    monkeypatch.setattr(safe_artifacts.stat, "S_ISREG", cancel_is_regular)

    with pytest.raises(KeyboardInterrupt, match="cancel regular validation"):
        safe_artifacts._open_regular(target, max_bytes=1024, label="receipt")

    assert file_fd is not None
    with pytest.raises(OSError):
        real_fstat(file_fd)
