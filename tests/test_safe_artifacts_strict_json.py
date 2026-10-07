from pathlib import Path

import pytest

from codeintel.safe_artifacts import read_json_object_bounded


def test_bounded_json_reader_rejects_nonfinite_constants(tmp_path: Path):
    for token in ("NaN", "Infinity", "-Infinity"):
        path = tmp_path / f"bad-{token.replace('-', 'neg')}.json"
        path.write_text('{"value":' + token + '}', encoding="utf-8")
        with pytest.raises(RuntimeError, match="invalid strict UTF-8 JSON"):
            read_json_object_bounded(path, max_bytes=1024, label="test artifact")


def test_bounded_json_reader_still_accepts_finite_numeric_json(tmp_path: Path):
    path = tmp_path / "ok.json"
    path.write_text('{"value":1.25}', encoding="utf-8")
    assert read_json_object_bounded(path, max_bytes=1024, label="test artifact") == {"value": 1.25}
