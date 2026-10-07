import os
from pathlib import Path

import pytest

from codeintel.freshness.production import _open_canonical_repo_file


@pytest.mark.skipif(
    not getattr(os, "O_NOFOLLOW", 0) or not getattr(os, "O_DIRECTORY", 0),
    reason="production scanner requires POSIX nofollow directory descriptors",
)
def test_canonical_repo_open_reads_normal_file(tmp_path: Path):
    repo = tmp_path / "repo"
    target = repo / "pkg" / "source.py"
    target.parent.mkdir(parents=True)
    target.write_text("value = 1\n", encoding="utf-8")

    with _open_canonical_repo_file(str(repo), "pkg/source.py") as handle:
        assert handle.read() == b"value = 1\n"


@pytest.mark.skipif(
    not getattr(os, "O_NOFOLLOW", 0) or not getattr(os, "O_DIRECTORY", 0),
    reason="production scanner requires POSIX nofollow directory descriptors",
)
def test_canonical_repo_open_refuses_symlink_component(tmp_path: Path):
    repo = tmp_path / "repo"
    outside = tmp_path / "outside"
    repo.mkdir()
    outside.mkdir()
    (outside / "secret.py").write_text("secret = True\n", encoding="utf-8")
    (repo / "pkg").symlink_to(outside, target_is_directory=True)

    with pytest.raises(OSError):
        _open_canonical_repo_file(str(repo), "pkg/secret.py")


def test_canonical_repo_open_rejects_parent_traversal(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()

    with pytest.raises(ValueError, match="unsafe components"):
        _open_canonical_repo_file(str(repo), "../outside.py")
