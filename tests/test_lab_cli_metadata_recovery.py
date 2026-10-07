"""Malformed persisted metadata is a state failure, not a programming traceback."""
import json
import sqlite3
import subprocess
import sys

import pytest

from codeintel.lab.retrieval import index_repository


@pytest.mark.parametrize("metadata", ["{", '{"duplicate":1,"duplicate":2}',
                                      '{"build_fingerprint":"contradictory"}'])
@pytest.mark.parametrize("command", ["index", "query"])
def test_malformed_generation_metadata_has_typed_nonmutating_cli_recovery(tmp_path, metadata, command):
    repo, state = tmp_path / "repo", tmp_path / "state"
    repo.mkdir()
    (repo / "a.py").write_text("def target(): return 1\n")
    index_repository(repo, state)
    connection = sqlite3.connect(state / "db.sqlite")
    try:
        connection.execute("UPDATE generations SET metadata_json=? WHERE is_active=1", (metadata,))
        connection.commit()
    finally:
        connection.close()
    before = (state / "db.sqlite").read_bytes()
    args = [sys.executable, "-m", "codeintel.lab.cli", command,
            "--repo", str(repo), "--state-dir", str(state)]
    if command == "query":
        args.append("target")
    result = subprocess.run(args, capture_output=True, timeout=5, check=False)
    assert result.returncode == 2
    assert result.stdout == b""
    assert json.loads(result.stderr)["code"] == "DerivedStateValidationError"
    assert b"Traceback" not in result.stderr
    assert str(tmp_path).encode() not in result.stderr
    assert (state / "db.sqlite").read_bytes() == before
