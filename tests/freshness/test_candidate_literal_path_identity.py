"""Candidate enumeration must not rewrite distinct physical POSIX filenames."""
import os
import subprocess

import pytest

from codeintel.core.security import RepoJail
from codeintel.freshness.production import (
    ProductionGenerationBarrier,
    _bounded_filesystem_candidate_paths,
    _bounded_git_candidate_paths,
)
from codeintel.lab.retrieval import LabError, index_repository, query_repository


pytestmark = pytest.mark.skipif(os.name != "posix", reason="literal POSIX backslash names")


def _prepare(tmp_path, git):
    repo = tmp_path / "repo"
    repo.mkdir()
    if git:
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "src").mkdir()
    (repo / "src/item.py").write_text("def canonical():\n    return 1\n")
    return repo


@pytest.mark.parametrize("git", [False, True])
def test_candidates_keep_distinct_literal_backslash_names(tmp_path, git):
    repo = _prepare(tmp_path, git)
    (repo / r"src\item.py").write_text("def hidden_target():\n    return 2\n")
    paths = (_bounded_git_candidate_paths(str(repo)) if git else
             _bounded_filesystem_candidate_paths(str(repo), RepoJail(str(repo))))
    assert set(paths) == {"src/item.py", r"src\item.py"}


@pytest.mark.parametrize("git", [False, True])
def test_scan_rejects_backslash_collision_instead_of_silently_omitting_source(tmp_path, git):
    repo = _prepare(tmp_path, git)
    (repo / r"src\item.py").write_text("def hidden_target():\n    return 2\n")
    with pytest.raises(RuntimeError, match="backslash|canonical"):
        ProductionGenerationBarrier.scan_repository(str(repo), RepoJail(str(repo)))


@pytest.mark.parametrize("git", [False, True])
def test_new_backslash_file_cannot_reuse_old_snapshot_as_fresh(tmp_path, git):
    repo = _prepare(tmp_path, git)
    state = tmp_path / "state"
    index_repository(repo, state)
    (repo / r"src\item.py").write_text("def hidden_target():\n    return 2\n")
    with pytest.raises(LabError, match="SOURCE_UNAVAILABLE"):
        query_repository(repo, state, "hidden_target")
