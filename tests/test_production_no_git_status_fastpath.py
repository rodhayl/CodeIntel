"""Live freshness never invokes the removed Git-status shortcut."""
from codeintel.freshness import hardened, production
from codeintel.service import create_default_service


def test_production_dirty_fast_path_is_removed(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("value = 1\n")
    def forbidden(*args, **kwargs):
        raise AssertionError("non-Git byte freshness must not invoke a Git process")
    monkeypatch.setattr(production, "run_bounded_process", forbidden)
    monkeypatch.setattr(hardened, "run_bounded_process", forbidden)
    with create_default_service(str(repo), str(tmp_path / "state")) as service:
        service.reindex()
        assert not hasattr(service.barrier, "_git_has_no_relevant_dirty_paths")
        assert service.barrier.check_freshness()[0] is True
        (repo / "a.py").write_text("value = 2\n")
        assert service.barrier.check_freshness()[0] is False
