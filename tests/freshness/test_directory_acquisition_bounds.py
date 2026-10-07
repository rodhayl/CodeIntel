"""Empty non-Git directories must consume their own traversal budget."""
import pytest

from codeintel.freshness import production as module
from codeintel.freshness.errors import SourceEnumerationError


class AllowAll:
    def is_path_allowed(self, path):
        return True


def test_empty_directory_fanout_refused_before_frontier_growth(tmp_path, monkeypatch):
    for index in range(5):
        (tmp_path / f'empty_{index}').mkdir()
    monkeypatch.setattr(module, 'MAX_INDEX_CANDIDATE_DIRECTORIES', 3, raising=False)
    with pytest.raises(SourceEnumerationError, match='directory-count limit'):
        module._bounded_filesystem_candidate_paths(str(tmp_path), AllowAll())


def test_directory_budget_counts_admissions_not_only_pending_frontier(tmp_path, monkeypatch):
    current = tmp_path
    for index in range(5):
        current = current / str(index)
        current.mkdir()
    monkeypatch.setattr(module, 'MAX_INDEX_CANDIDATE_DIRECTORIES', 3, raising=False)
    with pytest.raises(SourceEnumerationError, match='directory-count limit'):
        module._bounded_filesystem_candidate_paths(str(tmp_path), AllowAll())


def test_directory_budget_accepts_exact_limit_and_skips_denied_directories(tmp_path, monkeypatch):
    for index in range(3):
        directory = tmp_path / f'empty_{index}'
        directory.mkdir()
        (directory / 'source.py').write_text('pass')
    (tmp_path / 'ignored').mkdir()
    class IgnoreOne(AllowAll):
        def is_path_allowed(self, path):
            return path != 'ignored'
    monkeypatch.setattr(module, 'MAX_INDEX_CANDIDATE_DIRECTORIES', 3)
    assert len(module._bounded_filesystem_candidate_paths(str(tmp_path), IgnoreOne())) == 3
