from pathlib import Path

import pytest

from codeintel.core.contracts import MAX_TASK_QUERY_BYTES
from codeintel.core.models import Chunk
from codeintel.storage.production import (
    MAX_PRODUCTION_FTS_LIMIT,
    MAX_PRODUCTION_FTS_TERMS,
    ProductionSQLiteStore,
)


def _store(tmp_path: Path):
    state = tmp_path / "state"
    state.mkdir()
    (state / ".codeintel_state_layout").write_text("codeintel-state-v1\n", encoding="utf-8")
    store = ProductionSQLiteStore(str(state / "db.sqlite"))
    generation = store.create_generation("repo", 1, "abcdef0123456789")
    return store, generation


def _chunk(cid: str, gen: str, path: str, content: str, line: int = 1):
    return Chunk(
        chunk_id=cid,
        file_id=f"file_{cid}",
        generation_id=gen,
        rel_path=path,
        span=(line, 0, line, len(content.encode("utf-8"))),
        content=content,
        content_hash=f"hash_{cid}",
        entity_ids=[],
    )


def test_production_fts_validates_query_limit_and_generation_scope(tmp_path):
    store, generation = _store(tmp_path)
    with pytest.raises(ValueError, match="query must be a string"):
        store.search(123, generation_id=generation.generation_id)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="UTF-8 bytes"):
        store.search("x" * (MAX_TASK_QUERY_BYTES + 1), generation_id=generation.generation_id)
    for bad_limit in (0, -1, True, MAX_PRODUCTION_FTS_LIMIT + 1):
        with pytest.raises(ValueError, match="lexical limit"):
            store.search("needle", limit=bad_limit, generation_id=generation.generation_id)
    with pytest.raises(ValueError, match="generation_id"):
        store.search("needle", generation_id="../escape")
    store.close()


def test_fts_terms_are_unicode_aware_deduplicated_and_bounded():
    query = " ".join(["Cálculo", "cálculo"] + [f"term{i}" for i in range(200)])
    tokens = ProductionSQLiteStore._fts_tokens(query)
    assert tokens[0] == "Cálculo"
    assert len(tokens) == MAX_PRODUCTION_FTS_TERMS
    assert len({token.casefold() for token in tokens}) == len(tokens)
    assert all('"' not in token for token in tokens)


def test_unicode_identifier_is_retrievable_through_production_fts(tmp_path):
    store, generation = _store(tmp_path)
    super(ProductionSQLiteStore, store).index_chunks([
        _chunk("unicode", generation.generation_id, "src/calc.py", "def cálculo_urgente(): return 1")
    ])
    results = store.search("cálculo_urgente", generation_id=generation.generation_id)
    assert results
    assert results[0][0].chunk_id == "unicode"
    store.close()


def test_bm25_ties_have_deterministic_repository_order(tmp_path):
    store, generation = _store(tmp_path)
    super(ProductionSQLiteStore, store).index_chunks([
        _chunk("z", generation.generation_id, "src/z.py", "needle common body"),
        _chunk("a", generation.generation_id, "src/a.py", "needle common body"),
    ])
    first = store.search("needle common", limit=2, generation_id=generation.generation_id)
    second = store.search("needle common", limit=2, generation_id=generation.generation_id)
    assert [chunk.rel_path for chunk, _score in first] == ["src/a.py", "src/z.py"]
    assert [(chunk.chunk_id, score) for chunk, score in first] == [
        (chunk.chunk_id, score) for chunk, score in second
    ]
    store.close()


def test_storage_execution_failure_propagates_instead_of_becoming_empty_results(tmp_path):
    store, generation = _store(tmp_path)
    gen_id = generation.generation_id
    store.close()
    with pytest.raises(Exception):
        store.search("needle", generation_id=gen_id)


def test_corrupt_chunk_entity_metadata_fails_closed_on_retrieval(tmp_path):
    store, generation = _store(tmp_path)
    super(ProductionSQLiteStore, store).index_chunks([
        _chunk("corrupt", generation.generation_id, "src/a.py", "needle body")
    ])
    store._conn.execute(
        "UPDATE chunks SET entity_ids_json = ? WHERE generation_id = ? AND chunk_id = ?",
        ('{"not":"a-list"}', generation.generation_id, "corrupt"),
    )
    with pytest.raises(RuntimeError, match="entity_ids_json"):
        store.search("needle", generation_id=generation.generation_id)
    store.close()
