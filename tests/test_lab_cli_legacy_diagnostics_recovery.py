"""Historical diagnostic metadata retains structured CLI recovery."""
import json
import sqlite3
import subprocess
import sys

import pytest

from codeintel.lab.retrieval import index_repository


@pytest.mark.parametrize("invalid", [
    "python:TIMEOUT", [1], ["malformed"], ["unknown:TIMEOUT"],
    ["python:UNKNOWN_STATUS"], ["python:TIMEOUT"] * 2,
    ["python:TIMEOUT"] * 5,
])
@pytest.mark.parametrize("command", ["index", "query"])
def test_malformed_legacy_diagnostics_has_nonmutating_cli_recovery(tmp_path, invalid, command):
    repo, state = tmp_path / "repo", tmp_path / "state"
    repo.mkdir()
    (repo / "a.py").write_text("def target(): return 1\n")
    index_repository(repo, state)
    connection = sqlite3.connect(state / "db.sqlite")
    try:
        metadata = json.loads(connection.execute(
            "SELECT metadata_json FROM generations WHERE is_active=1").fetchone()[0])
        metadata["scip_failure_codes"] = invalid
        connection.execute("UPDATE generations SET metadata_json=? WHERE is_active=1",
                           (json.dumps(metadata),))
        connection.commit()
    finally:
        connection.close()
    before = (state / "db.sqlite").read_bytes()
    args = [sys.executable, "-m", "codeintel.lab.cli", command,
            "--repo", str(repo), "--state-dir", str(state)]
    if command == "query":
        args.append("target")
    result = subprocess.run(args, capture_output=True, timeout=10, check=False)
    assert result.returncode == 2
    assert result.stdout == b""
    error = json.loads(result.stderr)
    assert error["status"] == "ERROR"
    assert error["code"] == "DerivedStateValidationError"
    assert "new state directory" in error["recovery"]
    assert b"Traceback" not in result.stderr
    assert str(tmp_path).encode() not in result.stderr
    assert (state / "db.sqlite").read_bytes() == before
