"""Source type may change between STAGING capture and the parser's next open."""
import hashlib
import os
import subprocess
import sys

import pytest


@pytest.mark.skipif(os.name != "posix" or not hasattr(os, "mkfifo"), reason="requires POSIX FIFO")
def test_snapshot_bound_parser_read_rejects_replacement_fifo_without_blocking(tmp_path):
    source = tmp_path / "candidate.py"
    original = b"value = 1\n"
    source.write_bytes(original)
    expected_hash = hashlib.sha256(original).hexdigest()
    source.unlink()
    os.mkfifo(source)
    script = """
import sys
from codeintel.freshness.snapshot_binding import register_generation_snapshot, read_generation_source_bytes
register_generation_snapshot('gen_fifo_regression', {'candidate.py': sys.argv[2]})
try:
    read_generation_source_bytes(sys.argv[1], 'candidate.py', 'gen_fifo_regression')
except RuntimeError as exc:
    assert 'not a regular file' in str(exc), str(exc)
else:
    raise AssertionError('FIFO was accepted as source')
"""
    subprocess.run(
        [sys.executable, "-c", script, str(tmp_path), expected_hash],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=2.0, check=True,
    )
