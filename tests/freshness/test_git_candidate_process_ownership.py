"""Candidate enumeration shares bounded_process's lifetime and setup cleanup."""
import os
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

import codeintel.bounded_process as bounded
from codeintel.freshness import production


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX descriptors/process groups")
def test_git_candidate_timeout_still_applies_after_output_eof(tmp_path, monkeypatch):
    children = []
    real_popen = subprocess.Popen

    def inert_child(_argv, **kwargs):
        child = real_popen(
            [sys.executable, "-I", "-S", "-c",
             "import os,time; os.close(1); os.close(2); time.sleep(2)"], **kwargs,
        )
        children.append(child)
        return child

    monkeypatch.setattr(production, "resolve_tool_binary", lambda _name: "/trusted/git")
    monkeypatch.setattr(production, "_GIT_ENUMERATION_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(bounded.subprocess, "Popen", inert_child)
    started = time.monotonic()
    try:
        with pytest.raises(RuntimeError, match="timed out"):
            production._bounded_git_candidate_paths(str(tmp_path))
        assert time.monotonic() - started < 1.5
        assert len(children) == 1 and children[0].poll() is not None
        assert children[0].stdout.closed and children[0].stderr.closed
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=3)
            child.stdout.close()
            child.stderr.close()


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX process cleanup")
def test_git_candidate_reader_setup_failure_reaps_spawned_child(tmp_path, monkeypatch):
    children = []
    real_popen = subprocess.Popen

    def inert_child(_argv, **kwargs):
        child = real_popen(
            [sys.executable, "-I", "-S", "-c", "import time; time.sleep(2)"], **kwargs,
        )
        children.append(child)
        return child

    def fail_start(_reader):
        raise RuntimeError("injected candidate reader setup failure")

    monkeypatch.setattr(production, "resolve_tool_binary", lambda _name: "/trusted/git")
    monkeypatch.setattr(production, "_GIT_ENUMERATION_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(bounded.subprocess, "Popen", inert_child)
    monkeypatch.setattr(threading.Thread, "start", fail_start)
    try:
        with pytest.raises(RuntimeError, match="injected candidate reader setup failure"):
            production._bounded_git_candidate_paths(str(tmp_path))
        assert len(children) == 1 and children[0].poll() is not None
        assert children[0].stdout.closed and children[0].stderr.closed
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=3)
            child.stdout.close()
            child.stderr.close()


@pytest.mark.parametrize("stdout,count,pattern", [
    (b"a.py", 4, "incomplete NUL-delimited"),
    (b"a.py\x00b.py\x00", 10, "file-count limit"),
])
def test_git_candidate_rejects_incomplete_or_excess_paths(tmp_path, monkeypatch, stdout, count, pattern):
    monkeypatch.setattr(production, "resolve_tool_binary", lambda _name: "/trusted/git")
    monkeypatch.setattr(production, "MAX_INDEX_CANDIDATE_FILES", 1)
    monkeypatch.setattr(production, "run_bounded_process", lambda *_a, **_k: SimpleNamespace(
        returncode=0, timed_out=False, stdout=stdout, stderr=b"", observed_stdout_bytes=count,
    ))
    with pytest.raises(RuntimeError, match=pattern):
        production._bounded_git_candidate_paths(str(tmp_path))
