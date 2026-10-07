"""Selected stored-literal corruption keeps the CLI's derived-state boundary."""
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import codeintel
import pytest

from codeintel.lab.retrieval import index_repository


@pytest.mark.parametrize("content_hash", ["f" * 64, sqlite3.Binary(b"invalid-hash-type")],
                         ids=["mismatched-hash", "nontext-hash"])
def test_corrupt_selected_chunk_hash_has_redacted_nonmutating_cli_recovery(tmp_path, content_hash):
    repo, state = tmp_path / "repo", tmp_path / "state"
    repo.mkdir()
    (repo / "source.py").write_text("def private_marker():\n    return 123\n")
    index_repository(repo, state)
    connection = sqlite3.connect(state / "db.sqlite")
    try:
        connection.execute("UPDATE chunks SET content_hash = ?", (content_hash,))
        connection.commit()
    finally:
        connection.close()
    before = (state / "db.sqlite").read_bytes()
    record = tmp_path / "receipt.json"
    runtime_root = Path(codeintel.__file__).resolve().parent.parent
    program = (f"import sys; sys.path.insert(0, {str(runtime_root)!r}); "
               "from codeintel.lab.cli import main; main()")
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", program, "query", "--repo", str(repo),
         "--state-dir", str(state), "private_marker", "--record", str(record)],
        capture_output=True, timeout=10, check=False,
    )
    assert result.returncode == 2, result.stderr
    assert result.stdout == b""
    error = json.loads(result.stderr)
    assert error["status"] == "ERROR" and error["code"] == "DerivedStateValidationError"
    assert "new state directory" in error["recovery"]
    assert b"Traceback" not in result.stderr and b"private_marker" not in result.stderr
    assert str(tmp_path).encode() not in result.stderr
    assert not record.exists()
    assert (state / "db.sqlite").read_bytes() == before
