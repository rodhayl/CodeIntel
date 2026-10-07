from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest

from codeintel.core.tooling import sanitized_tool_environment
from codeintel.freshness.production import _bounded_git_candidate_paths


@pytest.mark.skipif(os.name != "posix" or shutil.which("git") is None, reason="requires POSIX git")
def test_production_git_enumeration_cannot_be_redirected_by_inherited_git_environment(
    tmp_path: Path, monkeypatch
):
    expected = tmp_path / "expected"
    attacker = tmp_path / "attacker"
    expected.mkdir()
    attacker.mkdir()
    git = shutil.which("git")
    clean_env = sanitized_tool_environment(os.environ.copy())
    subprocess.run([git, "init", "-q"], cwd=expected, env=clean_env, check=True)
    subprocess.run([git, "init", "-q"], cwd=attacker, env=clean_env, check=True)
    (expected / "expected.py").write_text("expected = True\n", encoding="utf-8")
    (attacker / "attacker.py").write_text("attacker = True\n", encoding="utf-8")
    subprocess.run([git, "add", "expected.py"], cwd=expected, env=clean_env, check=True)
    subprocess.run([git, "add", "attacker.py"], cwd=attacker, env=clean_env, check=True)

    monkeypatch.setenv("GIT_DIR", str(attacker / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(attacker))
    monkeypatch.setenv("GIT_INDEX_FILE", str(attacker / ".git" / "index"))
    monkeypatch.setenv("GIT_EXEC_PATH", str(tmp_path / "attacker-git-core"))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.excludesfile")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(tmp_path / "attacker-ignore"))

    assert _bounded_git_candidate_paths(str(expected)) == ["expected.py"]


def test_production_git_enumeration_fails_closed_without_trusted_git(tmp_path, monkeypatch):
    from codeintel.freshness import production as module

    monkeypatch.setattr(module, "resolve_tool_binary", lambda name: None)
    with pytest.raises(RuntimeError, match="trusted Git executable is unavailable"):
        _bounded_git_candidate_paths(str(tmp_path))


@pytest.mark.skipif(os.name != "posix" or shutil.which("git") is None, reason="requires POSIX git")
def test_production_git_enumeration_disables_repo_controlled_fsmonitor(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    marker = tmp_path / "fsmonitor-executed"
    helper = tmp_path / "fsmonitor.sh"
    helper.write_text(
        "#!/bin/sh\n"
        "printf 'EXECUTED\\n' >> \"$CODEINTEL_FSMONITOR_MARKER\"\n"
        "printf 'TOKEN\\0'\n",
        encoding="utf-8",
    )
    helper.chmod(0o755)
    git = shutil.which("git")
    clean_env = sanitized_tool_environment(os.environ.copy())
    subprocess.run([git, "init", "-q"], cwd=repo, env=clean_env, check=True)
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run([git, "add", "a.py"], cwd=repo, env=clean_env, check=True)
    subprocess.run([git, "config", "core.fsmonitor", str(helper)], cwd=repo, env=clean_env, check=True)
    monkeypatch.setenv("CODEINTEL_FSMONITOR_MARKER", str(marker))

    assert _bounded_git_candidate_paths(str(repo)) == ["a.py"]
    assert not marker.exists(), "repository-controlled fsmonitor helper executed"


@pytest.mark.skipif(os.name != "posix" or shutil.which("git") is None, reason="requires POSIX git")
def test_production_git_enumeration_does_not_open_repo_controlled_external_excludes_fifo(
    tmp_path: Path
):
    repo = tmp_path / "repo"
    repo.mkdir()
    external_excludes = tmp_path / "external-excludes.fifo"
    os.mkfifo(external_excludes)
    git = shutil.which("git")
    clean_env = sanitized_tool_environment(os.environ.copy())
    subprocess.run([git, "init", "-q"], cwd=repo, env=clean_env, check=True)
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run([git, "add", "a.py"], cwd=repo, env=clean_env, check=True)
    subprocess.run(
        [git, "config", "core.excludesfile", str(external_excludes)],
        cwd=repo,
        env=clean_env,
        check=True,
    )

    assert _bounded_git_candidate_paths(str(repo)) == ["a.py"]
