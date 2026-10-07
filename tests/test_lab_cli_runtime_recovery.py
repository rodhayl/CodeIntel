"""Expected derived-state failures must retain the CLI's structured error contract."""
import json
import sqlite3
import subprocess
import sys

import pytest

from codeintel.lab.retrieval import index_repository


@pytest.mark.parametrize("state_damage", ["future-version", "foreign-application", "missing-table"])
@pytest.mark.parametrize("command", ["index", "query"])
def test_cli_reports_incompatible_state_without_traceback(tmp_path, state_damage, command):
    repo, state = tmp_path / "repo", tmp_path / "state"
    repo.mkdir()
    (repo / "source.py").write_text("def target():\n    return 123\n")
    index_repository(repo, state)
    with sqlite3.connect(state / "db.sqlite") as connection:
        connection.execute({
            "future-version": "PRAGMA user_version=999",
            "foreign-application": "PRAGMA application_id=123",
            "missing-table": "DROP TABLE relations",
        }[state_damage])
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
    assert str(tmp_path).encode() not in result.stderr
    assert b"Traceback" not in result.stderr
    assert (state / "db.sqlite").read_bytes() == before
    if state_damage == "future-version":
        with sqlite3.connect(state / "db.sqlite") as connection:
            assert connection.execute("PRAGMA user_version").fetchone()[0] == 999
        connection.close()
