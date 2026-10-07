import os
from pathlib import Path

import pytest

from codeintel.core import tooling
from codeintel.core.tooling import resolve_tool_binary


def _make_executable(path: Path):
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)


def test_tool_resolution_never_uses_user_controlled_path(monkeypatch, tmp_path: Path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    malicious = bin_dir / "git"
    _make_executable(malicious)
    monkeypatch.setenv("PATH", str(bin_dir))

    monkeypatch.setattr(tooling, "_SYSTEM_TOOL_DIRS", ())
    assert resolve_tool_binary("git") is None


def test_retired_toolchain_override_and_non_git_tools_are_unavailable(tmp_path):
    with pytest.raises(TypeError):
        resolve_tool_binary("git", str(tmp_path))
    assert resolve_tool_binary("node") is None
    assert resolve_tool_binary("scip-typescript") is None


def test_tool_name_must_be_one_component():
    for bad in ("../tool", "/bin/tool", "a/b", "a\\b"):
        with pytest.raises(ValueError):
            resolve_tool_binary(bad)
