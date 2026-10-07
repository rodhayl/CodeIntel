from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest

from codeintel.core.tooling import resolve_tool_binary, sanitized_tool_environment

_SAFE_GIT_ENV_KEYS = {
    "GIT_CONFIG_NOSYSTEM",
    "GIT_CONFIG_GLOBAL",
    "GIT_ATTR_NOSYSTEM",
    "GIT_OPTIONAL_LOCKS",
    "GIT_TERMINAL_PROMPT",
    "GIT_PAGER",
    "GIT_CONFIG_COUNT",
    "GIT_CONFIG_KEY_0",
    "GIT_CONFIG_VALUE_0",
}


def test_sanitizer_replaces_inherited_git_authorities_without_mutating_source():
    source = {
        "PATH": "/tmp/attacker-bin",
        "GIT_DIR": "/tmp/attacker/.git",
        "GIT_WORK_TREE": "/tmp/attacker",
        "GIT_INDEX_FILE": "/tmp/attacker-index",
        "GIT_EXEC_PATH": "/tmp/attacker-git-core",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.excludesfile",
        "GIT_CONFIG_VALUE_0": "/tmp/attacker-ignore",
        "SAFE_MARKER": "present",
    }
    original = dict(source)
    env = sanitized_tool_environment(source)

    assert source == original
    assert env["SAFE_MARKER"] == "present"
    assert env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert env["GIT_CONFIG_GLOBAL"] == os.devnull
    assert env["GIT_ATTR_NOSYSTEM"] == "1"
    assert env["GIT_OPTIONAL_LOCKS"] == "0"
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GIT_PAGER"] == "cat"
    assert env["GIT_CONFIG_COUNT"] == "1"
    assert env["GIT_CONFIG_KEY_0"] == "core.fsmonitor"
    assert env["GIT_CONFIG_VALUE_0"] == "false"
    assert not [key for key in env if key.startswith("GIT_") and key not in _SAFE_GIT_ENV_KEYS]


@pytest.mark.skipif(os.name != "posix" or shutil.which("git") is None, reason="requires POSIX git")
def test_sanitized_git_cannot_be_redirected_away_from_requested_cwd(tmp_path: Path):
    git = resolve_tool_binary("git")
    assert git is not None
    expected = tmp_path / "expected"
    attacker = tmp_path / "attacker"
    expected.mkdir()
    attacker.mkdir()
    clean_env = sanitized_tool_environment({"PATH": os.environ.get("PATH", "")})
    subprocess.run([git, "init", "-q"], cwd=expected, env=clean_env, check=True)
    subprocess.run([git, "init", "-q"], cwd=attacker, env=clean_env, check=True)

    poisoned = os.environ.copy()
    poisoned.update(
        {
            "GIT_DIR": str(attacker / ".git"),
            "GIT_WORK_TREE": str(attacker),
            "GIT_INDEX_FILE": str(attacker / ".git" / "index"),
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "core.excludesfile",
            "GIT_CONFIG_VALUE_0": str(tmp_path / "attacker-ignore"),
        }
    )
    result = subprocess.run(
        [git, "rev-parse", "--show-toplevel"],
        cwd=expected,
        env=sanitized_tool_environment(poisoned),
        capture_output=True,
        text=True,
        check=True,
    )
    assert Path(result.stdout.strip()).resolve() == expected.resolve()


@pytest.mark.skipif(os.name != "posix" or shutil.which("git") is None, reason="requires POSIX git")
def test_sanitized_git_disables_repository_fsmonitor_helper(tmp_path: Path):
    git = resolve_tool_binary("git")
    assert git is not None
    repo = tmp_path / "repo"
    repo.mkdir()
    clean_env = sanitized_tool_environment({"PATH": os.environ.get("PATH", "")})
    subprocess.run([git, "init", "-q"], cwd=repo, env=clean_env, check=True)
    subprocess.run(
        [git, "config", "user.email", "fixture@example.invalid"],
        cwd=repo,
        env=clean_env,
        check=True,
    )
    subprocess.run(
        [git, "config", "user.name", "fixture"], cwd=repo, env=clean_env, check=True
    )
    (repo / "a.txt").write_text("x\n", encoding="utf-8")
    subprocess.run([git, "add", "a.txt"], cwd=repo, env=clean_env, check=True)
    subprocess.run([git, "commit", "-qm", "init"], cwd=repo, env=clean_env, check=True)

    marker = tmp_path / "fsmonitor-executed"
    helper = tmp_path / "fsmonitor.sh"
    helper.write_text(
        f"#!/bin/sh\nprintf x >> {str(marker)!r}\nexit 0\n", encoding="utf-8"
    )
    helper.chmod(0o700)
    subprocess.run(
        [git, "config", "core.fsmonitor", str(helper)],
        cwd=repo,
        env=clean_env,
        check=True,
    )

    # Prove the repository config is executable by this exact read command without the
    # controlled command-scope override.
    unsafe_env = dict(clean_env)
    unsafe_env.pop("GIT_CONFIG_COUNT", None)
    unsafe_env.pop("GIT_CONFIG_KEY_0", None)
    unsafe_env.pop("GIT_CONFIG_VALUE_0", None)
    subprocess.run(
        [git, "ls-files"], cwd=repo, env=unsafe_env, check=True, capture_output=True
    )
    assert marker.exists()
    marker.unlink()

    subprocess.run(
        [git, "ls-files"], cwd=repo, env=clean_env, check=True, capture_output=True
    )
    assert not marker.exists()
    local_name = subprocess.run(
        [git, "config", "--local", "--get", "user.name"],
        cwd=repo,
        env=clean_env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert local_name == "fixture"
