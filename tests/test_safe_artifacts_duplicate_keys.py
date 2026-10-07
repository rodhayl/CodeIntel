from pathlib import Path

import pytest

from codeintel.safe_artifacts import read_json_object_bounded


def test_bounded_json_reader_rejects_duplicate_object_keys(tmp_path: Path):
    path = tmp_path / "ambiguous.json"
    path.write_text('{"status":"FAIL","status":"PASS"}', encoding="utf-8")

    with pytest.raises(RuntimeError, match="duplicate JSON object key"):
        read_json_object_bounded(path, max_bytes=1024, label="receipt")


def test_bounded_json_reader_rejects_nested_duplicate_keys(tmp_path: Path):
    path = tmp_path / "nested.json"
    path.write_text('{"outer":{"x":1,"x":2}}', encoding="utf-8")

    with pytest.raises(RuntimeError, match="duplicate JSON object key"):
        read_json_object_bounded(path, max_bytes=1024, label="receipt")
