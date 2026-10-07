from pathlib import Path

import pytest

from codeintel.core.security import SecurityException, STATE_LAYOUT_MARKER, validate_trusted_state_dir


def test_trusted_state_dir_refuses_symlinked_layout_marker(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    external = tmp_path / "external-marker"
    external.write_text("2\n", encoding="utf-8")
    (state / STATE_LAYOUT_MARKER).symlink_to(external)

    with pytest.raises(SecurityException, match="state-layout marker"):
        validate_trusted_state_dir(str(repo), str(state))

    assert external.read_text(encoding="utf-8") == "2\n"


def test_trusted_state_dir_refuses_oversized_layout_marker(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    (state / STATE_LAYOUT_MARKER).write_text("2" * 128, encoding="utf-8")

    with pytest.raises(SecurityException, match="unexpectedly large"):
        validate_trusted_state_dir(str(repo), str(state))
