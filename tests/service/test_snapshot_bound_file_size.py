"""FileRecord sizes belong to the admitted snapshot, not a later pathname stat."""
import os

import pytest

from codeintel.core.security import FreshnessBusyError
from codeintel.service import create_default_service


def test_file_size_uses_verified_source_bytes_instead_of_pathname_stat(tmp_path, monkeypatch):
    repo, state = tmp_path / "repo", tmp_path / "state"
    repo.mkdir()
    source = repo / "a.py"
    raw = "def target(): return 'λ'\n".encode("utf-8")
    source.write_bytes(raw)
    original_getsize = os.path.getsize
    observed = []

    def raced_getsize(path):
        if os.fspath(path) == str(source):
            observed.append(path)
            return len(raw) + 1000
        return original_getsize(path)

    monkeypatch.setattr(os.path, "getsize", raced_getsize)
    with create_default_service(str(repo), str(state)) as service:
        result = service.reindex()
        row = service.graph_store._conn.execute(
            "SELECT size_bytes FROM files WHERE generation_id=? AND rel_path='a.py'",
            (result["generation_id"],),
        ).fetchone()
        assert row["size_bytes"] == len(raw)
        assert observed == []


def test_file_size_capture_rejects_snapshot_swap_before_parser_can_restore_it(tmp_path, monkeypatch):
    repo, state = tmp_path / "repo", tmp_path / "state"
    repo.mkdir()
    source = repo / "a.py"
    source.write_text("def target(): return 1\n")
    with create_default_service(str(repo), str(state)) as service:
        initial = service.reindex()
        expected = b"def target(): return 2\n"
        source.write_bytes(expected)
        create_staging = service.barrier.create_staging_generation
        parse_repository = service.merger.parse_repository

        def swap_after_snapshot():
            staging = create_staging()
            source.write_bytes(b"def target(): return 999999999\n")
            return staging

        def restore_before_parse(**kwargs):
            source.write_bytes(expected)
            return parse_repository(**kwargs)

        monkeypatch.setattr(service.barrier, "create_staging_generation", swap_after_snapshot)
        monkeypatch.setattr(service.merger, "parse_repository", restore_before_parse)
        with pytest.raises(FreshnessBusyError, match="STAGING snapshot"):
            service.reindex()
        assert service.generation_store.get_active_generation(service.repo_id).generation_id == initial["generation_id"]
        generations = service.generation_store.list_generations(service.repo_id)
        assert [generation.generation_id for generation in generations] == [initial["generation_id"]]
        assert service.barrier._rebuild_lock_file is None
