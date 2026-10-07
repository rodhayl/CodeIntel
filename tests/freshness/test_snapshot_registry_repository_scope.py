"""One staging owner cannot clear another repository's source-byte binding."""
import hashlib

import pytest

from tests.legacy_generation_fixture import legacy_generation_id
from codeintel.core.security import FreshnessBusyError
from codeintel.freshness.snapshot_binding import (
    clear_generation_snapshot, generation_snapshot_hashes, read_generation_source_text,
    register_generation_snapshot,
)
from codeintel.service.production import create_default_service
from codeintel.storage.sqlite_store import SQLiteStore


@pytest.fixture(autouse=True)
def retain_legacy_generation_collision(monkeypatch):
    # Keep the original registry isolation regression meaningful even though
    # new persistent IDs now include the repository identity.
    monkeypatch.setattr(SQLiteStore, '_new_generation_id', staticmethod(
        lambda repo_id, sequence, snapshot_hash: legacy_generation_id(sequence, snapshot_hash)))


@pytest.mark.parametrize("finish", ["abort", "commit"])
def test_other_repository_completion_keeps_staging_source_guard(tmp_path, finish):
    repo_a, repo_b = tmp_path / "a", tmp_path / "b"
    for repo in (repo_a, repo_b):
        repo.mkdir()
        (repo / "source.py").write_text("def target(): return 1\n")
    with create_default_service(str(repo_a), state_dir=str(tmp_path / "state-a")) as a, \
         create_default_service(str(repo_b), state_dir=str(tmp_path / "state-b")) as b:
        staging_b = b.barrier.create_staging_generation()
        try:
            if finish == "abort":
                staging_a = a.barrier.create_staging_generation()
                assert staging_a.generation.generation_id == staging_b.generation.generation_id
                a.barrier.abort_staging_generation(staging_a.generation)
            else:
                completed = a.reindex()
                assert completed["generation_id"] == staging_b.generation.generation_id
            (repo_b / "source.py").write_text("def target(): return 2\n")
            with pytest.raises(FreshnessBusyError, match="STAGING snapshot"):
                read_generation_source_text(str(repo_b), "source.py", staging_b.generation.generation_id)
        finally:
            b.barrier.abort_staging_generation(staging_b.generation)


@pytest.mark.parametrize("same_repo", [False, True])
@pytest.mark.parametrize("finish", ["abort", "commit", "close"])
def test_owner_cleanup_preserves_other_live_registration(tmp_path, same_repo, finish):
    repo_a = tmp_path / "a"
    repo_b = repo_a if same_repo else tmp_path / "b"
    for repo in {repo_a, repo_b}:
        repo.mkdir()
        (repo / "source.py").write_text("def target(): return 1\n")
    a = create_default_service(str(repo_a), state_dir=str(tmp_path / "state-a"))
    b = create_default_service(str(repo_b), state_dir=str(tmp_path / "state-b"))
    staging_b = None
    try:
        staging_b = b.barrier.create_staging_generation()
        generation_id = staging_b.generation.generation_id
        if finish == "commit":
            assert a.reindex()["generation_id"] == generation_id
        else:
            staging_a = a.barrier.create_staging_generation()
            assert staging_a.generation.generation_id == generation_id
            if finish == "abort":
                a.barrier.abort_staging_generation(staging_a.generation)
            else:
                a.close()
        assert generation_snapshot_hashes(generation_id, repo_root=str(repo_b)) == staging_b.current_hashes
        if not same_repo:
            assert generation_snapshot_hashes(generation_id, repo_root=str(repo_a)) is None
        (repo_b / "source.py").write_text("def target(): return 2\n")
        with pytest.raises(FreshnessBusyError, match="STAGING snapshot"):
            read_generation_source_text(str(repo_b), "source.py", generation_id)
        b.barrier.abort_staging_generation(staging_b.generation)
        staging_b = None
        assert generation_snapshot_hashes(generation_id, repo_root=str(repo_b)) is None
    finally:
        if staging_b is not None:
            b.barrier.abort_staging_generation(staging_b.generation)
        a.close()
        b.close()


def test_repository_scope_is_canonical_and_each_owner_is_idempotent(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    owner_a, owner_b = object(), object()
    hashes = {"source.py": "a" * 64}
    equivalent = str(repo / "child" / "..")
    register_generation_snapshot("gen_scope", hashes, repo_root=str(repo), owner=owner_a)
    register_generation_snapshot("gen_scope", hashes, repo_root=equivalent, owner=owner_a)
    register_generation_snapshot("gen_scope", hashes, repo_root=equivalent, owner=owner_b)
    try:
        clear_generation_snapshot("gen_scope", repo_root=equivalent, owner=owner_a)
        assert generation_snapshot_hashes("gen_scope", repo_root=str(repo)) == hashes
        failed_owner = object()
        with pytest.raises(RuntimeError, match="conflicting snapshot hashes"):
            register_generation_snapshot("gen_scope", {"source.py": "b" * 64},
                                         repo_root=str(repo), owner=failed_owner)
        clear_generation_snapshot("gen_scope", repo_root=str(repo), owner=failed_owner)
        assert generation_snapshot_hashes("gen_scope", repo_root=equivalent) == hashes
    finally:
        clear_generation_snapshot("gen_scope", repo_root=str(repo), owner=owner_a)
        clear_generation_snapshot("gen_scope", repo_root=str(repo), owner=owner_b)
    assert generation_snapshot_hashes("gen_scope", repo_root=str(repo)) is None


def test_explicit_legacy_unscoped_registration_still_guards_direct_reads(tmp_path):
    source = tmp_path / "source.py"
    source.write_text("value = 1\n")
    hashes = {"source.py": hashlib.sha256(source.read_bytes()).hexdigest()}
    register_generation_snapshot("gen_legacy", hashes)
    try:
        assert read_generation_source_text(str(tmp_path), "source.py", "gen_legacy") == "value = 1\n"
        source.write_text("value = 2\n")
        with pytest.raises(FreshnessBusyError, match="STAGING snapshot"):
            read_generation_source_text(str(tmp_path), "source.py", "gen_legacy")
    finally:
        clear_generation_snapshot("gen_legacy")


def test_legacy_registration_with_same_id_cannot_override_scoped_binding(tmp_path):
    source = tmp_path / "source.py"
    source.write_text("value = 1\n")
    expected = {"source.py": hashlib.sha256(source.read_bytes()).hexdigest()}
    owner = object()
    register_generation_snapshot("gen_shared_id", expected, repo_root=str(tmp_path), owner=owner)
    register_generation_snapshot("gen_shared_id", {"source.py": "0" * 64})
    try:
        assert read_generation_source_text(str(tmp_path), "source.py", "gen_shared_id") == "value = 1\n"
        clear_generation_snapshot("gen_shared_id")
        assert generation_snapshot_hashes("gen_shared_id", repo_root=str(tmp_path)) == expected
    finally:
        clear_generation_snapshot("gen_shared_id")
        clear_generation_snapshot("gen_shared_id", repo_root=str(tmp_path), owner=owner)
