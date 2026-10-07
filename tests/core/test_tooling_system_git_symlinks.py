from pathlib import Path

from codeintel.core import tooling


def _executable(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)


def test_resolve_git_accepts_symlink_within_trusted_system_roots(tmp_path, monkeypatch):
    bin_dir = tmp_path / "usr" / "local" / "bin"
    trusted_root = bin_dir / "versions"
    target = trusted_root / "git-real"
    _executable(target)
    bin_dir.mkdir(parents=True, exist_ok=True)
    (bin_dir / "git").symlink_to(target)

    monkeypatch.setattr(tooling, "_SYSTEM_TOOL_DIRS", (bin_dir,))
    monkeypatch.setattr(tooling, "_TRUSTED_SYSTEM_TOOL_TARGET_DIRS", (bin_dir,))

    assert tooling.resolve_tool_binary("git") == str(target.resolve())


def test_resolve_git_rejects_symlink_outside_trusted_system_roots(tmp_path, monkeypatch):
    bin_dir = tmp_path / "usr" / "local" / "bin"
    trusted_root = bin_dir / "versions"
    target = tmp_path / "home" / "attacker" / "git"
    _executable(target)
    bin_dir.mkdir(parents=True, exist_ok=True)
    (bin_dir / "git").symlink_to(target)

    monkeypatch.setattr(tooling, "_SYSTEM_TOOL_DIRS", (bin_dir,))
    monkeypatch.setattr(tooling, "_TRUSTED_SYSTEM_TOOL_TARGET_DIRS", (bin_dir,))

    assert tooling.resolve_tool_binary("git") is None
