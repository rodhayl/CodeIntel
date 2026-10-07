from pathlib import Path

import apsw
import pytest

from codeintel.storage.policy import configure_codeintel_connection


def test_foreign_database_is_rejected_before_persistent_journal_policy_mutation(tmp_path: Path):
    state = tmp_path / "state"
    state.mkdir()
    (state / ".codeintel_state_layout").write_text("2\n", encoding="utf-8")
    db = state / "db.sqlite"

    con = apsw.Connection(str(db))
    try:
        con.pragma("journal_mode", "DELETE")
        con.pragma("application_id", 12345)
        assert str(con.pragma("journal_mode")).lower() == "delete"

        with pytest.raises(RuntimeError, match="application_id"):
            configure_codeintel_connection(con)

        assert str(con.pragma("journal_mode")).lower() == "delete"
        assert int(con.pragma("application_id")) == 12345
    finally:
        con.close()

    assert not Path(str(db) + "-wal").exists()
