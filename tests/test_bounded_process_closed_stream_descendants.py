"""Validator completion owns descendants even when they release the output pipes."""

import os
import select
import signal
import sys
import threading

import pytest

from codeintel.bounded_process import run_bounded_process


pytestmark = pytest.mark.skipif(
    not hasattr(os, "fork") or not hasattr(os, "pidfd_open"),
    reason="requires Linux fork/process groups and pidfd exit notification",
)


@pytest.mark.parametrize("exit_code", [0, 7])
@pytest.mark.parametrize("redirect", [False, True])
def test_completion_terminates_descendants_without_inherited_pipes(tmp_path, exit_code, redirect):
    child_record = tmp_path / "child.pid"
    program = f"""
import os, signal
from pathlib import Path
ready_read, ready_write = os.pipe()
pid = os.fork()
if pid == 0:
    os.close(ready_read)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    if {redirect!r}:
        null = os.open(os.devnull, os.O_WRONLY)
        os.dup2(null, 1)
        os.dup2(null, 2)
        os.close(null)
    else:
        os.close(1)
        os.close(2)
    Path({str(child_record)!r}).write_text(str(os.getpid()))
    os.write(ready_write, b'R')
    os.close(ready_write)
    signal.pause()
    os._exit(99)
os.close(ready_write)
assert os.read(ready_read, 1) == b'R'
os.close(ready_read)
os.write(1, b'leader-complete')
os._exit({exit_code})
"""
    before_threads = set(threading.enumerate())
    pidfd = None
    try:
        result = run_bounded_process(
            [sys.executable, "-I", "-S", "-c", program],
            cwd=str(tmp_path), timeout_seconds=3, stdout_limit=256, stderr_limit=256,
        )
        assert result.returncode == exit_code
        assert result.timed_out is False
        assert result.stdout == b"leader-complete"
        assert result.observed_stdout_bytes == len(result.stdout)
        assert result.stderr == b""
        assert child_record.is_file()
        try:
            pidfd = os.pidfd_open(int(child_record.read_text()))
        except ProcessLookupError:
            pass  # The descendant has already exited and been reaped.
        else:
            ready, _, _ = select.select([pidfd], [], [], 2.0)
            assert ready, "validator returned with a live descendant after output EOF"
        assert not [
            thread for thread in threading.enumerate()
            if thread not in before_threads and thread.name.startswith("codeintel-validator-")
        ]
    finally:
        # Keep the baseline failure reproducible without leaving its child running.
        if pidfd is None and child_record.exists():
            try:
                pidfd = os.pidfd_open(int(child_record.read_text()))
            except ProcessLookupError:
                pass
        if pidfd is not None:
            try:
                signal.pidfd_send_signal(pidfd, signal.SIGKILL)
            except ProcessLookupError:
                pass
            select.select([pidfd], [], [], 2.0)
            os.close(pidfd)
