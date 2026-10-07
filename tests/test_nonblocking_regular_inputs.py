"""Reject FIFOs before a blocking open can bypass regular-file validation."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sys

import pytest
import codeintel

from codeintel.bounded_process import run_bounded_process
from codeintel.core.security import _read_state_layout_marker
from codeintel.safe_artifacts import read_regular_file_bounded, sha256_regular_file_bounded


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="requires named pipes")
@pytest.mark.parametrize("operation", ["read", "hash", "marker"])
def test_fifo_without_writer_is_rejected_without_hanging(tmp_path, operation):
    fifo = tmp_path / "input.fifo"
    os.mkfifo(fifo)
    source_root = Path(codeintel.__file__).resolve().parent.parent
    program = f"""
import sys
sys.path.insert(0, {str(source_root)!r})
from pathlib import Path
from codeintel.core.security import SecurityException, _read_state_layout_marker
from codeintel.safe_artifacts import read_regular_file_bounded, sha256_regular_file_bounded
path = Path({str(fifo)!r})
try:
    if {operation!r} == 'marker':
        _read_state_layout_marker(path)
    elif {operation!r} == 'read':
        read_regular_file_bounded(path, max_bytes=128, label='fifo')
    else:
        sha256_regular_file_bounded(path, max_bytes=128, label='fifo')
except (RuntimeError, SecurityException) as error:
    assert 'regular file' in str(error)
    print('REJECTED_NON_REGULAR', flush=True)
else:
    raise SystemExit('accepted a FIFO')
"""
    result = run_bounded_process(
        [sys.executable, "-I", "-S", "-c", program], cwd=str(tmp_path),
        timeout_seconds=2, stdout_limit=1024, stderr_limit=1024,
    )
    assert result.timed_out is False, "open blocked before the regular-file check"
    assert result.returncode == 0, result.stderr
    assert b"REJECTED_NON_REGULAR" in result.stdout


def test_regular_inputs_keep_exact_bytes_and_hash(tmp_path):
    path = tmp_path / "input.json"
    raw = b'{"value": 1}\r\n'
    path.write_bytes(raw)
    assert read_regular_file_bounded(path, max_bytes=len(raw), label="regular") == raw
    assert sha256_regular_file_bounded(path, max_bytes=len(raw), label="regular") == (
        hashlib.sha256(raw).hexdigest(), len(raw)
    )
    path.write_bytes(b"2\n")
    assert _read_state_layout_marker(path) == "2"


def test_symlink_inputs_still_fail_closed(tmp_path):
    target = tmp_path / "regular"
    target.write_bytes(b"2\n")
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(RuntimeError):
        read_regular_file_bounded(link, max_bytes=128, label="link")
    with pytest.raises(RuntimeError):
        sha256_regular_file_bounded(link, max_bytes=128, label="link")
