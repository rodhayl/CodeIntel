"""Filesystem candidates use literal tilde names rather than user-home syntax."""
import subprocess
from pathlib import Path

import pytest

from codeintel.core.security import RepoJail, SecurityException
from codeintel.lab.retrieval import index_repository, query_repository, verify_packet


@pytest.mark.parametrize("relative", ["~/source.py", "~literal_user/source.py", "~source.py"])
@pytest.mark.parametrize("git", [False, True])
def test_tilde_candidates_keep_physical_identity_and_refresh(tmp_path, monkeypatch, relative, git):
    repo, state = tmp_path / "repo", tmp_path / "state"
    repo.mkdir()
    source = repo / relative
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("def local_target(): return 1\n")
    outside = tmp_path / "outside-home"
    outside.mkdir()
    (outside / "source.py").write_text("DO NOT READ HOME AS REPOSITORY SOURCE\n")
    monkeypatch.setenv("HOME", str(outside))
    if git:
        subprocess.run(["git", "init", "-q", str(repo)], check=True)

    real_expanduser = Path.expanduser

    def reject_candidate_expansion(path):
        assert not str(path).startswith("~"), "literal repository candidate reached home expansion"
        return real_expanduser(path)

    # Explicit repo/state configuration here is absolute. Relative source
    # candidates must never be passed to home/username resolution.
    monkeypatch.setattr(Path, "expanduser", reject_candidate_expansion)
    indexed, _ = index_repository(repo, state)
    assert indexed["files"] == 1
    packet, _ = query_repository(repo, state, "local_target")
    assert packet["selected"][0]["path"] == relative
    assert "return 1" in packet["selected"][0]["source"]
    verify_packet(repo, packet)
    source.write_text("def local_target(): return 2\n")
    changed, _ = query_repository(repo, state, "local_target")
    assert changed["refreshed"] is True
    assert "return 2" in changed["selected"][0]["source"]
    verify_packet(repo, changed)


def test_external_absolute_and_parent_traversal_still_rejected(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("outside\n")
    jail = RepoJail(str(repo))
    for candidate in (str(outside), "../outside.py"):
        with pytest.raises(SecurityException):
            jail.safe_relpath(candidate)


def test_explicit_repository_configuration_still_expands_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    repo = home / "repo"
    repo.mkdir(parents=True)
    (repo / "source.py").write_text("value = 1\n")
    monkeypatch.setenv("HOME", str(home))
    jail = RepoJail("~/repo")
    assert jail.repo_root == repo
    assert jail.safe_relpath("source.py") == "source.py"
