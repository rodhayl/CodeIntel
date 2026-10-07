from pathlib import Path

import apsw

from codeintel.storage.policy import is_codeintel_db


def test_codeintel_db_ownership_refuses_symlinked_layout_marker(tmp_path: Path):
    state = tmp_path / "state"
    state.mkdir()
    db = state / "db.sqlite"
    con = apsw.Connection(str(db))
    try:
        external = tmp_path / "marker.txt"
        external.write_text("2\n", encoding="utf-8")
        marker = state / ".codeintel_state_layout"
        marker.symlink_to(external)
        assert is_codeintel_db(con) is False

        marker.unlink()
        marker.write_text("2\n", encoding="utf-8")
        assert is_codeintel_db(con) is True
    finally:
        con.close()
