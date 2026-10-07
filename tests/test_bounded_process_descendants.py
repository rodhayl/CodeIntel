"""Real-process regressions for inherited validator output pipes."""
from __future__ import annotations

import os
from pathlib import Path
import signal
import sys
import threading
import time

import pytest

from codeintel.bounded_process import run_bounded_process


pytestmark = pytest.mark.skipif(os.name != "posix", reason="requires POSIX process groups")


def _kill_recorded_child(path: Path) -> None:
    # Clean up even against the original, defective implementation.
    if path.exists():
        try:
            os.kill(int(path.read_text()), signal.SIGKILL)
        except ProcessLookupError:
            pass


@pytest.mark.parametrize("leader_exits", [True, False])
def test_timeout_cleans_descendant_after_leader_exit(tmp_path, leader_exits):
    child_pid = tmp_path / "child.pid"
    program = f"""
import os, signal, sys
from pathlib import Path
ready_read, ready_write = os.pipe()
pid = os.fork()
if pid == 0:
    os.close(ready_read)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    Path({str(child_pid)!r}).write_text(str(os.getpid()))
    os.write(ready_write, b'R')
    os.close(ready_write)
    while True:
        signal.pause()
os.close(ready_write)
assert os.read(ready_read, 1) == b'R'
os.close(ready_read)
print('leader-ready', flush=True)
if {leader_exits!r}:
    sys.exit(0)
while True:
    signal.pause()
"""
    before_threads = set(threading.enumerate())
    try:
        started = time.monotonic()
        result = run_bounded_process(
            [sys.executable, "-I", "-S", "-c", program], cwd=str(tmp_path),
            timeout_seconds=2.0, stdout_limit=1024, stderr_limit=1024,
        )
        elapsed = time.monotonic() - started
        assert result.timed_out is True
        assert b"leader-ready" in result.stdout
        assert elapsed < 7.0
        assert not [
            thread for thread in threading.enumerate()
            if thread not in before_threads and thread.name.startswith("codeintel-validator-")
        ]
    finally:
        _kill_recorded_child(child_pid)


def test_short_lived_descendant_output_is_not_discarded(tmp_path):
    program = """
import os, time
pid = os.fork()
if pid == 0:
    time.sleep(0.05)
    os.write(1, b'late-child-output')
    os._exit(0)
os._exit(0)
"""
    result = run_bounded_process(
        [sys.executable, "-I", "-S", "-c", program], cwd=str(tmp_path),
        timeout_seconds=3, stdout_limit=1024, stderr_limit=1024,
    )
    assert result.returncode == 0
    assert result.timed_out is False
    assert result.stdout == b"late-child-output"
    assert result.observed_stdout_bytes == len(result.stdout)


def test_parallel_output_retains_prefix_and_observed_counts(tmp_path):
    program = "import os; os.write(1, b'x' * 200000); os.write(2, b'y' * 180000)"
    result = run_bounded_process(
        [sys.executable, "-I", "-S", "-c", program], cwd=str(tmp_path),
        timeout_seconds=5, stdout_limit=257, stderr_limit=193,
    )
    assert result.returncode == 0
    assert result.timed_out is False
    assert result.stdout == b"x" * 257
    assert result.stderr == b"y" * 193
    assert result.observed_stdout_bytes == 200000
    assert result.observed_stderr_bytes == 180000
