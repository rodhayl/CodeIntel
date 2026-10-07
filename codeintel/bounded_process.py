"""Bounded subprocess execution for production-host validators.

The release runner executes only canonical CodeIntel validators, but a validator or
its dependencies can still misbehave.  This helper drains stdout/stderr concurrently
while retaining only a fixed prefix, records the true observed byte counts, sanitizes
interpreter-injection environment variables, and terminates the whole child process
group at the ownership boundary, including after normal leader exit.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import os
import signal
import subprocess
import threading
import time
from typing import Mapping, Optional, Sequence

from codeintel.core.tooling import sanitized_tool_environment


@dataclass(frozen=True)
class BoundedProcessResult:
    args: tuple[str, ...]
    returncode: int
    stdout: bytes
    stderr: bytes
    observed_stdout_bytes: int
    observed_stderr_bytes: int
    timed_out: bool


def _drain(pipe, limit: int, result: dict, key: str) -> None:
    captured = bytearray()
    observed = 0
    error: BaseException | None = None
    try:
        while True:
            block = pipe.read(64 * 1024)
            if not block:
                break
            observed += len(block)
            remaining = limit - len(captured)
            if remaining > 0:
                captured.extend(block[:remaining])
    except BaseException as exc:  # surfaced to the caller after child cleanup
        error = exc
    finally:
        try:
            pipe.close()
        except Exception:
            pass
        result[key] = (bytes(captured), observed, error)


def _terminate_process_tree(proc: subprocess.Popen, *, isolate_process_group: bool = True) -> None:
    # A reaped session leader does not imply its process group has exited.
    # Descendants may still own the output pipes or ignore SIGTERM.
    if os.name == "posix" and isolate_process_group:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            proc.wait(timeout=5.0)
            return
        except OSError:
            if proc.poll() is not None:
                raise
            proc.terminate()
    else:
        if proc.poll() is not None:
            return
        proc.terminate()
    try:
        proc.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        pass
    # Always finish POSIX group cleanup, even if TERM ended only the leader.
    if os.name == "posix" and isolate_process_group:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError:
            if proc.poll() is not None:
                raise
            proc.kill()
    elif proc.poll() is None:
        proc.kill()
    proc.wait(timeout=5.0)


def run_bounded_process(
    argv: Sequence[str],
    *,
    cwd: str,
    timeout_seconds: float,
    stdout_limit: int,
    stderr_limit: int,
    env: Optional[Mapping[str, str]] = None,
    isolate_process_group: bool = True,
    pass_fds: Sequence[int] = (),
) -> BoundedProcessResult:
    # Inherited mode is only for a nested helper inside another bounded owner's
    # process group. That owner must clean all descendants on exit/timeout. This
    # function never kills its own/parent group in inherited mode.
    if type(isolate_process_group) is not bool:
        raise ValueError("isolate_process_group must be boolean")
    descriptors = tuple(pass_fds)
    if len(set(descriptors)) != len(descriptors) or any(type(fd) is not int or fd < 3 for fd in descriptors):
        raise ValueError("inherited descriptors must be unique non-stdio integers")
    if descriptors and os.name != "posix":
        raise ValueError("inherited descriptor transport requires POSIX")
    for fd in descriptors:
        os.fstat(fd)
    args = tuple(str(part) for part in argv)
    if not args:
        raise ValueError("bounded process argv must not be empty")
    if not isinstance(stdout_limit, int) or isinstance(stdout_limit, bool) or stdout_limit < 0:
        raise ValueError("stdout_limit must be a non-negative integer")
    if not isinstance(stderr_limit, int) or isinstance(stderr_limit, bool) or stderr_limit < 0:
        raise ValueError("stderr_limit must be a non-negative integer")
    if (
        not isinstance(timeout_seconds, (int, float))
        or isinstance(timeout_seconds, bool)
        or not math.isfinite(float(timeout_seconds))
        or timeout_seconds <= 0
    ):
        raise ValueError("timeout_seconds must be a finite positive number")

    source_env = dict(os.environ if env is None else env)
    child_env = sanitized_tool_environment(source_env)
    proc = subprocess.Popen(
        list(args),
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=child_env,
        shell=False,
        start_new_session=(os.name == "posix" and isolate_process_group),
        **({"pass_fds": descriptors} if descriptors else {}),
    )
    assert proc.stdout is not None and proc.stderr is not None
    drained: dict = {}
    readers: dict[str, threading.Thread] = {}
    streams = (("stdout", proc.stdout, stdout_limit), ("stderr", proc.stderr, stderr_limit))
    timed_out = False
    deadline = time.monotonic() + float(timeout_seconds)
    try:
        # Reader construction/start can fail after Popen succeeds. Keep both
        # operations inside the same cleanup boundary as process execution.
        for key, pipe, limit in streams:
            reader = threading.Thread(
                target=_drain,
                args=(pipe, limit, drained, key),
                daemon=True,
                name=f"codeintel-validator-{key}",
            )
            readers[key] = reader
            reader.start()
        proc.wait(timeout=max(0.0, deadline - time.monotonic()))
        # The same deadline covers inherited output handles after leader exit.
        for reader in readers.values():
            reader.join(timeout=max(0.0, deadline - time.monotonic()))
        timed_out = any(reader.is_alive() for reader in readers.values())
    except subprocess.TimeoutExpired:
        timed_out = True
    finally:
        try:
            # Output EOF and leader exit do not prove descendant exit: children
            # can redirect or close their streams while staying in our group.
            if (
                (os.name == "posix" and isolate_process_group)
                or proc.poll() is None
                or any(reader.is_alive() for reader in readers.values())
            ):
                _terminate_process_tree(proc, isolate_process_group=isolate_process_group)
        finally:
            for reader in readers.values():
                if reader.ident is not None:
                    reader.join(timeout=5.0)
            for key, pipe, _limit in streams:
                reader = readers.get(key)
                # Never contend for a BufferedReader lock held by a live drain.
                if reader is None or not reader.is_alive():
                    pipe.close()
            if any(reader.is_alive() for reader in readers.values()):
                raise RuntimeError("validator output drain thread did not terminate")

    stdout, observed_stdout, stdout_error = drained.get("stdout", (b"", 0, None))
    stderr, observed_stderr, stderr_error = drained.get("stderr", (b"", 0, None))
    if stdout_error is not None:
        raise RuntimeError(f"validator stdout capture failed: {stdout_error}") from stdout_error
    if stderr_error is not None:
        raise RuntimeError(f"validator stderr capture failed: {stderr_error}") from stderr_error
    return BoundedProcessResult(
        args=args,
        returncode=int(proc.returncode),
        stdout=stdout,
        stderr=stderr,
        observed_stdout_bytes=int(observed_stdout),
        observed_stderr_bytes=int(observed_stderr),
        timed_out=timed_out,
    )


__all__ = ["BoundedProcessResult", "run_bounded_process"]
