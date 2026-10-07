"""Unsafe state lock/lease artifacts are structured recovery, without writes through them."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from codeintel.lab.retrieval import index_repository


@pytest.mark.skipif(os.name != "posix", reason="POSIX no-follow lock boundaries")
@pytest.mark.parametrize("command", ["index", "query"])
@pytest.mark.parametrize("artifact", ["rebuild-lock", "lease-directory", "lease-file"])
@pytest.mark.parametrize("kind", ["symlink", "fifo", "wrong-type"])
def test_unsafe_state_artifact_has_redacted_recovery(tmp_path, command, artifact, kind):
    repo, state = tmp_path / "repo", tmp_path / "state"
    repo.mkdir()
    source = repo / "source.py"
    source.write_text("def target(): return 1\n")
    index_repository(repo, state)
    if artifact == "rebuild-lock":
        path = state / "rebuild.lock"
    elif artifact == "lease-directory":
        path = state / "generation_leases"
    else:
        path = next((state / "generation_leases").glob("*.lock"))
    # Preserve the old artifact separately; do not conflate rejection with reset.
    path.rename(tmp_path / "original-artifact")
    outside = tmp_path / "outside-target"
    if artifact == "lease-directory":
        outside.mkdir()
        sentinel = outside / "sentinel"
    else:
        sentinel = outside
    sentinel.write_bytes(b"unchanged outside target")
    if kind == "symlink":
        path.symlink_to(outside, target_is_directory=artifact == "lease-directory")
    elif kind == "fifo":
        os.mkfifo(path)
    elif artifact == "lease-directory":
        path.write_bytes(b"not a directory")
    else:
        path.mkdir()
    if artifact == "rebuild-lock" and command == "query":
        # A query of an unchanged snapshot does not need the rebuild lock.
        # Force a legitimate rebuild rather than demanding an unused lock.
        source.write_text("def target(): return 2\n")
    before_source, before_target = source.read_bytes(), sentinel.read_bytes()
    receipt = tmp_path / "receipt.json"
    args = [sys.executable, "-m", "codeintel.lab.cli", command,
            "--repo", str(repo), "--state-dir", str(state), "--record", str(receipt)]
    if command == "query":
        args.append("target")
    result = subprocess.run(args, capture_output=True, timeout=10, check=False)
    assert result.returncode == 2, result.stderr
    assert result.stdout == b"" and not receipt.exists()
    error = json.loads(result.stderr)
    assert error["code"] == "DerivedStateValidationError"
    assert "new state directory" in error["recovery"]
    assert b"Traceback" not in result.stderr
    assert str(tmp_path).encode() not in result.stderr
    assert source.read_bytes() == before_source
    assert sentinel.read_bytes() == before_target
