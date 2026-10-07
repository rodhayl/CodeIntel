"""Known no-follow database refusals use the CLI's storage recovery contract."""
import json
import os
import subprocess
import sys

import pytest


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlink and FIFO fixture")
@pytest.mark.parametrize("kind", ["symlink", "fifo", "directory"])
@pytest.mark.parametrize("command", ["index", "query"])
def test_unsafe_database_path_is_structured_and_nonmutating(tmp_path, kind, command):
    repo, state = tmp_path / "repo", tmp_path / "state"
    repo.mkdir()
    state.mkdir()
    source = repo / "sample.py"
    source.write_text("def target(): return 1\n")
    marker = state / ".codeintel_state_layout"
    marker.write_text("2\n")
    external = tmp_path / "external"
    external.write_bytes(b"external content must remain unchanged\n")
    db = state / "db.sqlite"
    if kind == "symlink":
        db.symlink_to(external)
    elif kind == "fifo":
        os.mkfifo(db)
    else:
        db.mkdir()
    source_before, external_before = source.read_bytes(), external.read_bytes()
    db_before = db.lstat()
    args = [sys.executable, "-m", "codeintel.lab.cli", command,
            "--repo", str(repo), "--state-dir", str(state)]
    if command == "query":
        args.append("target")
    result = subprocess.run(args, capture_output=True, timeout=5, check=False)
    assert result.returncode == 2 and result.stdout == b""
    error = json.loads(result.stderr)
    assert error["code"] == "DerivedStateValidationError"
    assert "new state directory" in error["recovery"]
    assert b"Traceback" not in result.stderr and str(tmp_path).encode() not in result.stderr
    assert source.read_bytes() == source_before
    assert external.read_bytes() == external_before
    assert marker.read_bytes() == b"2\n"
    assert sorted(item.name for item in state.iterdir()) == [".codeintel_state_layout", "db.sqlite"]
    after = db.lstat()
    assert (after.st_ino, after.st_mode, after.st_size) == (db_before.st_ino, db_before.st_mode, db_before.st_size)
