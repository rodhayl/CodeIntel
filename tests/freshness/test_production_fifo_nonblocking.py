import os
import subprocess
import sys

import pytest


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX FIFO support required")
def test_production_repo_file_open_cannot_block_on_fifo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    os.mkfifo(repo / "candidate.py")

    script = (
        "import os,stat,sys; "
        "from codeintel.freshness.production import _open_canonical_repo_file; "
        "h=_open_canonical_repo_file(sys.argv[1], 'candidate.py'); "
        "print(stat.S_ISFIFO(os.fstat(h.fileno()).st_mode)); "
        "h.close()"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script, str(repo)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=2.0,
        check=True,
        text=True,
    )

    assert completed.stdout.strip() == "True"
