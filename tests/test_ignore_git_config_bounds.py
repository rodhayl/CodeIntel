from pathlib import Path

import pytest

from codeintel.bounded_process import BoundedProcessResult
from codeintel.freshness import hardened as module
from codeintel.freshness.hardened import HardenedProductionGenerationBarrier


def _barrier(repo: Path):
    barrier = object.__new__(HardenedProductionGenerationBarrier)
    barrier.repo_root = str(repo)
    return barrier


def _result(*, stdout=b"", stderr=b"", observed=None, returncode=0, timed_out=False):
    return BoundedProcessResult(
        args=("git", "config"),
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        observed_stdout_bytes=len(stdout) if observed is None else observed,
        observed_stderr_bytes=len(stderr),
        timed_out=timed_out,
    )


def test_ignore_git_config_rejects_observed_output_beyond_path_limit(monkeypatch, tmp_path: Path):
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(
        module,
        "run_bounded_process",
        lambda *args, **kwargs: _result(stdout=b"x", observed=module._MAX_IGNORE_PATH_BYTES + 1),
    )

    with pytest.raises(RuntimeError, match="path exceeds production byte limit"):
        _barrier(tmp_path)._compute_ignore_fingerprint()


def test_ignore_git_config_rejects_timeout(monkeypatch, tmp_path: Path):
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(
        module,
        "run_bounded_process",
        lambda *args, **kwargs: _result(returncode=-9, timed_out=True),
    )

    with pytest.raises(RuntimeError, match="timed out"):
        _barrier(tmp_path)._compute_ignore_fingerprint()


def test_ignore_git_config_rejects_non_utf8_path(monkeypatch, tmp_path: Path):
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(
        module,
        "run_bounded_process",
        lambda *args, **kwargs: _result(stdout=b"\xff\n"),
    )

    with pytest.raises(RuntimeError, match="not valid UTF-8"):
        _barrier(tmp_path)._compute_ignore_fingerprint()


def test_ignore_fingerprint_hashes_bounded_effective_info_exclude_file(monkeypatch, tmp_path: Path):
    (tmp_path / ".git").mkdir()
    external = tmp_path / ".git" / "info-exclude"
    external.write_text("build/\n", encoding="utf-8")
    stdout = (str(external) + "\n").encode("utf-8")
    monkeypatch.setattr(
        module,
        "run_bounded_process",
        lambda *args, **kwargs: _result(stdout=stdout),
    )
    barrier = _barrier(tmp_path)

    first = barrier._compute_ignore_fingerprint()
    external.write_text("build/\ndist/\n", encoding="utf-8")
    second = barrier._compute_ignore_fingerprint()
    assert first != second
