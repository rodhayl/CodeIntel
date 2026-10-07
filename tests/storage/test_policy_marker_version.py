from __future__ import annotations

import os

from codeintel.core.security import MAX_STATE_LAYOUT_MARKER_BYTES, STATE_LAYOUT_VERSION
from codeintel.storage.policy import is_codeintel_db


class _Connection:
    def __init__(self, path):
        self.path = path

    def db_filename(self, name):
        assert name == "main"
        return str(self.path)


def _db(tmp_path):
    db = tmp_path / "db.sqlite"
    db.touch()
    return db


def test_policy_rejects_wrong_state_layout_version(tmp_path):
    db = _db(tmp_path)
    (tmp_path / ".codeintel_state_layout").write_text("999\n", encoding="utf-8")
    assert is_codeintel_db(_Connection(db)) is False


def test_policy_rejects_non_utf8_state_layout_marker(tmp_path):
    db = _db(tmp_path)
    (tmp_path / ".codeintel_state_layout").write_bytes(b"\xff\xfe")
    assert is_codeintel_db(_Connection(db)) is False


def test_policy_rejects_oversized_state_layout_marker(tmp_path):
    db = _db(tmp_path)
    (tmp_path / ".codeintel_state_layout").write_bytes(
        b"2" + b" " * MAX_STATE_LAYOUT_MARKER_BYTES
    )
    assert is_codeintel_db(_Connection(db)) is False


def test_policy_rejects_symlinked_state_layout_marker(tmp_path):
    if not hasattr(os, "symlink"):
        return
    db = _db(tmp_path)
    target = tmp_path / "real-marker"
    target.write_text(STATE_LAYOUT_VERSION + "\n", encoding="utf-8")
    (tmp_path / ".codeintel_state_layout").symlink_to(target)
    assert is_codeintel_db(_Connection(db)) is False


def test_policy_accepts_current_state_layout_version(tmp_path):
    db = _db(tmp_path)
    (tmp_path / ".codeintel_state_layout").write_text(
        STATE_LAYOUT_VERSION + "\n", encoding="utf-8"
    )
    assert is_codeintel_db(_Connection(db)) is True
