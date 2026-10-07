import shutil
import subprocess

import pytest

from codeintel.freshness.hardened import HardenedProductionGenerationBarrier


def _git(git, cwd, *args):
    return subprocess.run(
        [git, *args],
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
        text=True,
    )


def test_ignore_fingerprint_supports_linked_git_worktree(tmp_path):
    git = shutil.which("git")
    if git is None:
        pytest.skip("git executable required")

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(git, repo, "init")
    _git(git, repo, "config", "user.email", "codeintel@example.invalid")
    _git(git, repo, "config", "user.name", "CodeIntel Test")
    (repo / ".gitignore").write_text("ignored.tmp\n", encoding="utf-8")
    (repo / "tracked.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(git, repo, "add", ".gitignore", "tracked.py")
    _git(git, repo, "commit", "-m", "seed")

    worktree = tmp_path / "linked"
    _git(git, repo, "worktree", "add", "-b", "linked-test", str(worktree))
    assert (worktree / ".git").is_file()

    barrier = object.__new__(HardenedProductionGenerationBarrier)
    barrier.repo_root = str(worktree)
    fingerprint = barrier._compute_ignore_fingerprint()

    assert len(fingerprint) == 64
    int(fingerprint, 16)
