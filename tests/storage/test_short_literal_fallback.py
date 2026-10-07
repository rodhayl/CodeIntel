"""Short literals are a bounded scan of the pinned generation, never bigram FTS."""
import pytest

from codeintel.lab.retrieval import index_repository, query_repository, verify_packet
from codeintel.storage.production import ProductionSQLiteStore
from tests.storage.test_generation_scoped_fts import add_generation


@pytest.mark.parametrize('query', ['id', 'ID', '雪山', 'xy', '"xy"', 'x'])
def test_short_literal_query_returns_verified_source(tmp_path, query):
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / 'short.py').write_text('XY = "id"\nNOTE = "雪山"\n', encoding='utf-8')
    state = tmp_path / 'state'
    index_repository(repo, state)
    packet, _ = query_repository(repo, state, query)
    assert packet['status'] == 'OK'
    assert packet['selected'][0]['path'] == 'short.py'
    verify_packet(repo, packet)


def test_short_terms_are_not_sent_to_trigram_fts():
    assert ProductionSQLiteStore._fts_tokens('id 雪山 x') == []
    assert ProductionSQLiteStore._fts_expression('id 雪山 x') is None
    assert ProductionSQLiteStore._fts_tokens('long id 雪山') == ['long']


def test_short_literal_scope_order_and_limit(tmp_path):
    with ProductionSQLiteStore(str(tmp_path / 'db.sqlite')) as store:
        old, _ = add_generation(store, 'repo', 1, ['id obsolete'])
        active, _ = add_generation(store, 'repo', 2, ['id second', 'id first'])
        store.activate_generation(active.generation_id)
        rows = store.search('ID', generation_id=active.generation_id, limit=1)
        assert len(rows) == 1
        assert rows[0][0].rel_path == 'repo_0.py'
        assert rows[0][1] == 1.0
        assert store.search('id', generation_id='unknown') == []
        assert all(chunk.generation_id == active.generation_id
                   for chunk, _ in store.search('id'))
        assert store.search('id', generation_id=old.generation_id)


def _content_reads(store):
    reads = []
    store._conn.set_exec_trace(lambda _c, sql, bindings:
                              (reads.append(bindings) if sql.startswith('SELECT c.*, -1.0')
                               else None, True)[1])
    return reads


def test_short_scan_stops_at_chunk_cap_before_reading_later_content(tmp_path, monkeypatch):
    from codeintel.storage import production
    monkeypatch.setattr(production, 'MAX_SHORT_LITERAL_CHUNKS', 3)
    with ProductionSQLiteStore(str(tmp_path / 'db.sqlite')) as store:
        generation, chunks = add_generation(store, 'repo', 1, [f'id value {i}' for i in range(7)])
        reads = _content_reads(store)
        rows = store.search('id', generation_id=generation.generation_id)
        expected = sorted(chunks, key=lambda chunk: chunk.chunk_id)[:3]
        assert len(reads) == 3
        assert {chunk.chunk_id for chunk, _ in rows} == {chunk.chunk_id for chunk in expected}
        assert [chunk.rel_path for chunk, _ in rows] == sorted(chunk.rel_path for chunk in expected)
        assert not store._conn.in_transaction


def test_short_scan_byte_cap_counts_utf8_and_stops_before_fetch(tmp_path, monkeypatch):
    from codeintel.storage import production
    contents = [f'雪山 id {i}' for i in range(5)]
    one_size = len(contents[0].encode('utf-8'))
    monkeypatch.setattr(production, 'MAX_SHORT_LITERAL_BYTES', 2 * one_size)
    with ProductionSQLiteStore(str(tmp_path / 'db.sqlite')) as store:
        generation, chunks = add_generation(store, 'repo', 1, contents)
        reads = _content_reads(store)
        rows = store.search('雪山', generation_id=generation.generation_id)
        expected = sorted(chunks, key=lambda chunk: chunk.chunk_id)[:2]
        assert len(reads) == 2
        assert sum(len(chunk.content.encode('utf-8')) for chunk, _ in rows) == 2 * one_size
        assert {chunk.chunk_id for chunk, _ in rows} == {chunk.chunk_id for chunk in expected}
        assert not store._conn.in_transaction
        monkeypatch.setattr(production, 'MAX_SHORT_LITERAL_BYTES', one_size - 1)
        reads.clear()
        assert store.search('id', generation_id=generation.generation_id) == []
        assert not reads


def test_unscoped_short_scan_shares_global_cap_and_round_robins(tmp_path, monkeypatch):
    from codeintel.storage import production
    monkeypatch.setattr(production, 'MAX_SHORT_LITERAL_CHUNKS', 3)
    with ProductionSQLiteStore(str(tmp_path / 'db.sqlite')) as store:
        a, _ = add_generation(store, 'a', 1, ['id one', 'id two'])
        b, _ = add_generation(store, 'b', 1, ['id three', 'id four'])
        store.activate_generation(a.generation_id)
        store.activate_generation(b.generation_id)
        reads = _content_reads(store)
        rows = store.search('id')
        assert len(reads) == 3
        assert [chunk.generation_id for chunk, _ in rows] == [a.generation_id, b.generation_id, a.generation_id]


def test_long_query_never_uses_short_fallback_and_accents_remain_significant(tmp_path, monkeypatch):
    with ProductionSQLiteStore(str(tmp_path / 'db.sqlite')) as store:
        generation, _ = add_generation(store, 'repo', 1, ['id café'])
        def unexpected(*args):
            pytest.fail('Long-term FTS query invoked the short-literal scan')
        monkeypatch.setattr(store, '_search_short_literal_rows', unexpected)
        assert store.search('CAFÉ', generation_id=generation.generation_id)
        assert store.search('cafe', generation_id=generation.generation_id) == []
        assert store.search('absent id', generation_id=generation.generation_id) == []


def test_short_term_count_is_bounded_and_punctuation_is_not_fts_syntax(tmp_path):
    from itertools import product
    from codeintel.storage.production import MAX_PRODUCTION_FTS_TERMS
    terms = [''.join(pair) for pair in product('abcdefghijklmnop', repeat=2)]
    terms = terms[:MAX_PRODUCTION_FTS_TERMS + 1]
    with ProductionSQLiteStore(str(tmp_path / 'db.sqlite')) as store:
        generation, _ = add_generation(store, 'repo', 1, [terms[-1]])
        assert store.search(' '.join(terms), generation_id=generation.generation_id) == []
        assert store.search('"' + terms[-1] + '"*', generation_id=generation.generation_id)
        assert store.search('"*()', generation_id=generation.generation_id) == []


def test_short_scan_releases_snapshot_on_read_failure(tmp_path, monkeypatch):
    with ProductionSQLiteStore(str(tmp_path / 'db.sqlite')) as store:
        generation, _ = add_generation(store, 'repo', 1, ['id literal'])
        original = store._execute
        def fail_content(sql, *args):
            if sql.startswith('SELECT c.*, -1.0'):
                raise OSError('injected content read failure')
            return original(sql, *args)
        monkeypatch.setattr(store, '_execute', fail_content)
        with pytest.raises(OSError, match='injected'):
            store.search('id', generation_id=generation.generation_id)
        assert not store._conn.in_transaction


def test_shared_fts_expression_preserves_base_and_production_long_queries(tmp_path):
    from codeintel.storage.policy import fts_expression
    from codeintel.storage.sqlite_store import SQLiteStore
    assert fts_expression([]) is None
    assert fts_expression(['some/path.py', 'term']) == '"some/path.py term" OR ("some/path.py" OR "term")'
    with ProductionSQLiteStore(str(tmp_path / 'db.sqlite')) as store:
        generation, _ = add_generation(store, 'repo', 1, ['needle other', 'some/path.py'])
        for query in ['needle', 'needle other', 'some/path.py', '"needle"*']:
            actual = store.search(query, generation_id=generation.generation_id)
            base = SQLiteStore.search(store, query, generation_id=generation.generation_id)
            assert [(c.chunk_id, score) for c, score in actual] == [(c.chunk_id, score) for c, score in base]


def test_short_fallback_does_not_bypass_chunk_metadata_validation(tmp_path):
    with ProductionSQLiteStore(str(tmp_path / 'db.sqlite')) as store:
        generation, _ = add_generation(store, 'repo', 1, ['id literal'])
        store._conn.execute("UPDATE chunks SET entity_ids_json = '{}' WHERE generation_id = ?",
                            (generation.generation_id,))
        with pytest.raises(RuntimeError, match='entity_ids_json'):
            store.search('id', generation_id=generation.generation_id)
