import os
from pathlib import Path

import pytest

from codeintel.core.contracts import MAX_IGNORE_FINGERPRINT_FILE_BYTES
from codeintel.freshness.hardened import HardenedProductionGenerationBarrier


def _barrier(repo: Path):
    barrier = object.__new__(HardenedProductionGenerationBarrier)
    barrier.repo_root = str(repo)
    return barrier


def test_ignore_fingerprint_is_stable_for_regular_bounded_files(tmp_path: Path):
    ignore = tmp_path / ".gitignore"
    ignore.write_text("*.pyc\n.cache/\n", encoding="utf-8")
    barrier = _barrier(tmp_path)

    first = barrier._compute_ignore_fingerprint()
    second = barrier._compute_ignore_fingerprint()
    assert first == second

    ignore.write_text("*.pyc\n.cache/\nbuild/\n", encoding="utf-8")
    assert barrier._compute_ignore_fingerprint() != first


def test_ignore_fingerprint_refuses_symlink_instead_of_reading_host_target(tmp_path: Path):
    external = tmp_path / "external-ignore"
    external.write_text("secret-host-pattern\n", encoding="utf-8")
    os.symlink(external, tmp_path / ".gitignore")

    with pytest.raises(RuntimeError, match="cannot be opened safely"):
        _barrier(tmp_path)._compute_ignore_fingerprint()


def test_ignore_fingerprint_refuses_special_file_without_blocking(tmp_path: Path):
    fifo = tmp_path / ".gitignore"
    os.mkfifo(fifo)

    with pytest.raises(RuntimeError, match="must be a regular file"):
        _barrier(tmp_path)._compute_ignore_fingerprint()


def test_ignore_fingerprint_refuses_oversized_file(tmp_path: Path):
    ignore = tmp_path / ".gitignore"
    ignore.write_bytes(b"x" * (MAX_IGNORE_FINGERPRINT_FILE_BYTES + 1))

    with pytest.raises(RuntimeError, match="exceeds production byte limit"):
        _barrier(tmp_path)._compute_ignore_fingerprint()
