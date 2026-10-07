"""Fault injection must not orphan an actual spawned validator."""
from __future__ import annotations

import subprocess
import sys
import threading

import pytest

import codeintel.bounded_process as bounded


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
@pytest.mark.parametrize("failure_stage", ["construct", "start"])
def test_reader_setup_failure_reaps_child_and_closes_pipes(tmp_path, monkeypatch, stream, failure_stage):
    children = []
    readers = []
    real_popen = subprocess.Popen
    real_thread = threading.Thread
    real_start = threading.Thread.start
    target_name = f"codeintel-validator-{stream}"

    def tracked_popen(*args, **kwargs):
        child = real_popen(*args, **kwargs)
        children.append(child)
        return child

    def construct_reader(*args, **kwargs):
        if kwargs.get("name") == target_name and failure_stage == "construct":
            raise RuntimeError("injected reader setup failure")
        thread = real_thread(*args, **kwargs)
        readers.append(thread)
        return thread

    def start_reader(thread):
        if thread.name == target_name and failure_stage == "start":
            raise RuntimeError("injected reader setup failure")
        return real_start(thread)

    monkeypatch.setattr(bounded.subprocess, "Popen", tracked_popen)
    monkeypatch.setattr(real_thread, "start", start_reader)
    monkeypatch.setattr(bounded.threading, "Thread", construct_reader)
    try:
        with pytest.raises(RuntimeError, match="injected reader setup failure"):
            bounded.run_bounded_process(
                [sys.executable, "-I", "-S", "-c", "import time; time.sleep(60)"],
                cwd=str(tmp_path), timeout_seconds=2,
                stdout_limit=128, stderr_limit=128,
            )
        assert len(children) == 1
        child = children[0]
        assert child.poll() is not None, "setup failure left the validator running"
        assert child.stdout.closed and child.stderr.closed
        assert not any(thread.is_alive() for thread in readers)
    finally:
        # Leave no process, pipe, or reader behind when testing the broken version.
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)
        for thread in readers:
            if thread.ident is not None:
                thread.join(timeout=5)
        for child in children:
            child.stdout.close()
            child.stderr.close()
