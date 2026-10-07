import subprocess

import pytest

from codeintel.freshness import production as module


class _AllowAllJail:
    @staticmethod
    def is_path_allowed(_path: str) -> bool:
        return True


def test_git_candidate_enumeration_refuses_bounded_output_overflow(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "candidate.py").write_text("print('x')\n", encoding="utf-8")
    monkeypatch.setattr(module, "MAX_GIT_CANDIDATE_OUTPUT_BYTES", 1)

    with pytest.raises(RuntimeError, match="output byte limit"):
        module._bounded_git_candidate_paths(str(repo))


def test_filesystem_candidate_enumeration_refuses_file_count_overflow(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("a", encoding="utf-8")
    (repo / "b.py").write_text("b", encoding="utf-8")
    monkeypatch.setattr(module, "MAX_INDEX_CANDIDATE_FILES", 1)

    with pytest.raises(RuntimeError, match="file-count limit"):
        module._bounded_filesystem_candidate_paths(str(repo), _AllowAllJail())


def test_filesystem_candidate_enumeration_accepts_exact_limit(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("a", encoding="utf-8")
    (repo / "b.py").write_text("b", encoding="utf-8")
    monkeypatch.setattr(module, "MAX_INDEX_CANDIDATE_FILES", 2)

    assert sorted(module._bounded_filesystem_candidate_paths(str(repo), _AllowAllJail())) == [
        "a.py",
        "b.py",
    ]
