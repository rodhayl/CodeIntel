"""Verification must read stable regular source, not merely a matching byte stream."""
import json
import os
import subprocess
import sys

import pytest

from codeintel.lab.retrieval import index_repository, query_repository, serialize


@pytest.mark.skipif(os.name != "posix", reason="POSIX named pipe regression")
@pytest.mark.parametrize("feed_original_bytes", [False, True])
def test_verify_rejects_selected_file_replaced_with_fifo(tmp_path, feed_original_bytes):
    repo, state = tmp_path / "repo", tmp_path / "state"
    repo.mkdir()
    source = repo / "source.py"
    original = b"def target():\n    return 123\n"
    source.write_bytes(original)
    index_repository(repo, state)
    packet, _ = query_repository(repo, state, "target")
    packet_path = tmp_path / "packet.json"
    packet_path.write_bytes(serialize(packet))
    source.unlink()
    os.mkfifo(source)
    writer = None
    try:
        if feed_original_bytes:
            writer = os.open(source, os.O_RDWR | os.O_NONBLOCK)
            os.write(writer, original)
        result = subprocess.run(
            [sys.executable, "-m", "codeintel.lab.cli", "verify", "--repo", str(repo),
             "--packet", str(packet_path)],
            capture_output=True, timeout=2, check=False,
        )
    finally:
        if writer is not None:
            os.close(writer)
    assert result.returncode == 2, result.stderr
    assert result.stdout == b""
    assert json.loads(result.stderr)["code"] == "SOURCE_UNAVAILABLE"

