import hashlib
from pathlib import Path

import pytest

from codeintel.core.security import FreshnessBusyError
from codeintel.freshness.snapshot_binding import (
    clear_generation_snapshot,
    generation_snapshot_hashes,
    read_generation_source_text,
    register_generation_snapshot,
)
from codeintel.parsing.semantic_merger import SemanticMerger


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_snapshot_bound_source_rejects_bytes_different_from_staging(tmp_path: Path):
    generation_id = "gen_snapshot_bound"
    source = tmp_path / "app.py"
    original = "def value():\n    return 1\n"
    source.write_text(original, encoding="utf-8")
    register_generation_snapshot(generation_id, {"app.py": _sha(original)})
    try:
        source.write_text("def value():\n    return 2\n", encoding="utf-8")
        with pytest.raises(FreshnessBusyError, match="no longer matches STAGING snapshot"):
            read_generation_source_text(str(tmp_path), "app.py", generation_id)
    finally:
        clear_generation_snapshot(generation_id)


def test_semantic_merger_fails_closed_when_source_changes_after_snapshot(tmp_path: Path):
    generation_id = "gen_parse_bound"
    source = tmp_path / "app.py"
    original = "def value():\n    return 1\n"
    source.write_text(original, encoding="utf-8")
    register_generation_snapshot(generation_id, {"app.py": _sha(original)})
    merger = SemanticMerger()
    merger.ts_parser.parse_file = lambda **_kwargs: ([], [], [])
    try:
        source.write_text("def value():\n    return 999\n", encoding="utf-8")
        with pytest.raises(FreshnessBusyError, match="STAGING snapshot"):
            merger.parse_repository(
                repo_id="repo_test",
                repo_root=str(tmp_path),
                generation_id=generation_id,
                file_paths=["app.py"],
            )
    finally:
        clear_generation_snapshot(generation_id)




def test_snapshot_registration_rejects_conflicting_identity():
    generation_id = "gen_conflict"
    register_generation_snapshot(generation_id, {"a.py": "0" * 64})
    try:
        with pytest.raises(RuntimeError, match="conflicting snapshot hashes"):
            register_generation_snapshot(generation_id, {"a.py": "1" * 64})
        assert generation_snapshot_hashes(generation_id) == {"a.py": "0" * 64}
    finally:
        clear_generation_snapshot(generation_id)
