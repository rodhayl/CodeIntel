import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import codeintel.freshness.production as production_module
from codeintel.core.security import FreshnessBusyError, RepoJail
from codeintel.freshness.production import ProductionGenerationBarrier


def _barrier(root: Path) -> ProductionGenerationBarrier:
    barrier = object.__new__(ProductionGenerationBarrier)
    barrier.repo_root = str(root.resolve())
    barrier.jail = RepoJail(str(root))
    return barrier


def test_production_scan_is_deterministic_and_deduplicated(tmp_path):
    (tmp_path / "z.py").write_text("z = 1\n", encoding="utf-8")
    (tmp_path / "a.py").write_text("a = 1\n", encoding="utf-8")
    (tmp_path / "m.txt").write_text("m\n", encoding="utf-8")

    hashes = _barrier(tmp_path).scan_worktree()
    assert list(hashes) == ["a.py", "m.txt", "z.py"]


def test_production_scan_rejects_non_regular_fifo_before_open(tmp_path):
    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFO unavailable on this platform")
    os.mkfifo(tmp_path / "events.py")
    with pytest.raises(RuntimeError, match="not a regular file"):
        _barrier(tmp_path).scan_worktree()


def test_production_scan_enforces_hard_prechunk_file_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(production_module, "MAX_INDEX_SOURCE_FILE_BYTES", 8)
    (tmp_path / "large.py").write_bytes(b"x" * 9)
    with pytest.raises(RuntimeError, match="exceeds production file limit"):
        _barrier(tmp_path).scan_worktree()


def test_production_scan_excludes_generated_repowise_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(production_module, "MAX_INDEX_SOURCE_FILE_BYTES", 8)
    cache = tmp_path / ".repowise"
    cache.mkdir()
    (cache / "knowledge-graph.json").write_bytes(b"x" * 9)
    (tmp_path / "training.jsonl").write_bytes(b"x" * 9)
    (tmp_path / "source.py").write_bytes(b"x = 1\n")

    assert list(_barrier(tmp_path).scan_worktree()) == ["source.py"]


def test_binary_or_invalid_utf8_code_fails_closed_but_binary_noncode_is_omitted(tmp_path):
    (tmp_path / "bad.py").write_bytes(b"\xff\xfe\x00")
    with pytest.raises(RuntimeError, match="regular UTF-8 text"):
        _barrier(tmp_path).scan_worktree()

    (tmp_path / "bad.py").unlink()
    (tmp_path / "asset.dat").write_bytes(b"\x00\x01\x02")
    (tmp_path / "good.py").write_text("ok = True\n", encoding="utf-8")
    hashes = _barrier(tmp_path).scan_worktree()
    assert "good.py" in hashes
    assert "asset.dat" not in hashes


def test_symlink_escape_is_revalidated_at_point_of_use(tmp_path):
    outside = tmp_path.parent / f"outside-{tmp_path.name}.py"
    outside.write_text("secret = 1\n", encoding="utf-8")
    try:
        (tmp_path / "escape.py").symlink_to(outside)
        with pytest.raises(RuntimeError, match="escaped the production jail"):
            _barrier(tmp_path).scan_worktree()
    finally:
        outside.unlink(missing_ok=True)


def test_mutation_during_file_read_fails_with_freshness_busy(tmp_path, monkeypatch):
    path = tmp_path / "race.py"
    path.write_text("value = 1\n", encoding="utf-8")
    real_fstat = production_module.os.fstat
    calls = {"count": 0}

    def unstable_fstat(fd):
        stat_result = real_fstat(fd)
        calls["count"] += 1
        if calls["count"] == 2:
            return SimpleNamespace(
                st_ino=stat_result.st_ino,
                st_size=stat_result.st_size,
                st_mtime_ns=stat_result.st_mtime_ns + 1,
                st_mtime=stat_result.st_mtime,
            )
        return stat_result

    monkeypatch.setattr(production_module.os, "fstat", unstable_fstat)
    with pytest.raises(FreshnessBusyError, match="changed while being read"):
        _barrier(tmp_path).scan_worktree()
