from pathlib import Path

import pytest

from codeintel.core.identity import make_repo_id


def test_repo_id_is_identical_for_real_path_and_symlink_alias(tmp_path: Path):
    real = tmp_path / "repo"
    real.mkdir()
    alias = tmp_path / "repo-link"
    alias.symlink_to(real, target_is_directory=True)

    assert make_repo_id(str(real)) == make_repo_id(str(alias))
    assert make_repo_id(str(real / ".")) == make_repo_id(str(real))


def test_repo_id_rejects_empty_or_nul_path():
    for bad in ("", "   ", "bad\x00path"):
        with pytest.raises(ValueError, match="Repository path"):
            make_repo_id(bad)
