from pathlib import Path

import pytest

from codeintel.safe_artifacts import read_regular_file_bounded


def test_bounded_reader_refuses_symlinked_parent_directory(tmp_path: Path):
    real = tmp_path / "real"
    real.mkdir()
    artifact = real / "receipt.json"
    artifact.write_bytes(b'{}')
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)

    with pytest.raises(RuntimeError):
        read_regular_file_bounded(alias / "receipt.json", max_bytes=32, label="receipt")

    assert read_regular_file_bounded(artifact, max_bytes=32, label="receipt") == b'{}'
