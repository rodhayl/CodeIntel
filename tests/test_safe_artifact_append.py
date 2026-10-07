from __future__ import annotations

import os
from pathlib import Path
import stat
import time

import pytest

from codeintel.safe_artifacts import append_regular_file_nofollow


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX descriptor semantics")
def test_append_rejects_raced_leaf_symlink_without_touching_target(tmp_path: Path):
    target = tmp_path / "redirected-target"
    link = tmp_path / "telemetry.jsonl"
    link.symlink_to(target)

    with pytest.raises(RuntimeError):
        append_regular_file_nofollow(link, b"record\n", label="telemetry")

    assert not target.exists()


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX descriptor semantics")
def test_append_rejects_symlinked_parent_component(tmp_path: Path):
    real_parent = tmp_path / "real"
    real_parent.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real_parent, target_is_directory=True)

    with pytest.raises((RuntimeError, OSError)):
        append_regular_file_nofollow(alias / "telemetry.jsonl", b"record\n", label="telemetry")

    assert not (real_parent / "telemetry.jsonl").exists()


@pytest.mark.skipif(
    os.name != "posix" or not hasattr(os, "mkfifo"),
    reason="requires POSIX FIFO semantics",
)
def test_append_rejects_fifo_without_blocking(tmp_path: Path):
    fifo = tmp_path / "telemetry.fifo"
    os.mkfifo(fifo, 0o600)
    started = time.monotonic()
    with pytest.raises(RuntimeError):
        append_regular_file_nofollow(fifo, b"record\n", label="telemetry")
    assert time.monotonic() - started < 1.0


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX descriptor semantics")
def test_append_preserves_order_and_private_mode(tmp_path: Path):
    output = tmp_path / "telemetry.jsonl"
    append_regular_file_nofollow(output, b"first\n", label="telemetry")
    os.chmod(output, 0o666)
    append_regular_file_nofollow(output, b"second\n", label="telemetry")

    assert output.read_bytes() == b"first\nsecond\n"
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
