"""Production HEAD reads must use the same trusted boundary as candidate scans."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from codeintel.core.tooling import resolve_tool_binary, sanitized_tool_environment
from codeintel.freshness.hardened import HardenedProductionGenerationBarrier
from codeintel.freshness import production


def _barrier(repo):
    barrier = object.__new__(HardenedProductionGenerationBarrier)
    barrier.repo_root = str(repo)
    return barrier


@pytest.mark.skipif(os.name != "posix", reason="inert shell fixture requires POSIX")
def test_hardened_head_ignores_path_and_git_environment_redirection(tmp_path, monkeypatch):
    git = resolve_tool_binary("git")
    if git is None:
        pytest.skip("trusted Git is unavailable")
    clean_env = sanitized_tool_environment()
    heads = []
    for name in ("expected", "other"):
        repo = tmp_path / name
        repo.mkdir()
        subprocess.run([git, "init", "-q"], cwd=repo, env=clean_env, check=True)
        subprocess.run(
            [git, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
             "-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", name],
            cwd=repo, env=clean_env, check=True,
        )
        heads.append(subprocess.check_output([git, "rev-parse", "HEAD"], cwd=repo, env=clean_env).decode().strip())
    assert heads[0] != heads[1]

    marker = tmp_path / "path-git-executed"
    tools = tmp_path / "bin"
    tools.mkdir()
    impostor = tools / "git"
    impostor.write_text('#!/bin/sh\nprintf executed > "$CODEINTEL_HEAD_MARKER"\n', encoding="utf-8")
    impostor.chmod(0o700)
    monkeypatch.setenv("CODEINTEL_HEAD_MARKER", str(marker))
    monkeypatch.setenv("PATH", str(tools) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "other" / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(tmp_path / "other"))
    monkeypatch.setenv("GIT_INDEX_FILE", str(tmp_path / "other" / ".git" / "index"))

    assert _barrier(tmp_path / "expected")._get_git_head() == heads[0]
    assert not marker.exists()


@pytest.mark.parametrize("change", [
    {"timed_out": True}, {"returncode": 1}, {"observed_stdout_bytes": 257},
    {"stdout": b"not-a-commit\n"}, {"stdout": b"\xff"},
])
def test_production_head_discards_failed_or_invalid_bounded_results(tmp_path, monkeypatch, change):
    (tmp_path / ".git").mkdir()
    result = dict(returncode=0, timed_out=False, stdout=b"a" * 40 + b"\n", observed_stdout_bytes=41)
    result.update(change)
    monkeypatch.setattr(production, "resolve_tool_binary", lambda _name: "/trusted/git")

    def run(argv, **kwargs):
        assert argv == ["/trusted/git", "--no-optional-locks", "rev-parse", "HEAD"]
        assert kwargs == dict(cwd=str(tmp_path), timeout_seconds=2.0, stdout_limit=256, stderr_limit=1024)
        return SimpleNamespace(**result)

    monkeypatch.setattr(production, "run_bounded_process", run)
    assert _barrier(tmp_path)._get_git_head() is None


def test_production_head_without_trusted_git_does_not_start_a_process(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(production, "resolve_tool_binary", lambda _name: None)
    monkeypatch.setattr(production, "run_bounded_process", lambda *_a, **_k: pytest.fail("unexpected process"))
    assert _barrier(tmp_path)._get_git_head() is None


def test_optional_head_does_not_swallow_programming_runtime_error(tmp_path, monkeypatch):
    (tmp_path / '.git').mkdir()
    monkeypatch.setattr(production, 'resolve_tool_binary', lambda _name: '/trusted/git')
    def broken(*args, **kwargs):
        raise RuntimeError('unexpected head implementation failure')
    monkeypatch.setattr(production, 'run_bounded_process', broken)
    with pytest.raises(RuntimeError, match='unexpected head implementation failure'):
        _barrier(tmp_path)._get_git_head()
