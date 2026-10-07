"""Acquisition limits are independent of the final packet/candidate count."""
from __future__ import annotations

import pytest

from codeintel.core.identity import canonical_hash
from codeintel.core.models import Chunk, Entity, EntityKind, FileRecord
from codeintel.retrieval.hybrid import HybridRetriever
from codeintel.storage.sqlite_store import SQLiteStore


@pytest.fixture
def store():
    with SQLiteStore(':memory:') as value:
        yield value


def populate(store, *, files=1, chunks_per_file=1, name='shared'):
    generation = store.create_generation('repo', 1, 'a' * 64)
    entities, chunks = [], []
    for file_index in range(files):
        fid = f'file_{file_index:04}'
        path = f'module_{file_index:04}.py'
        eid = f'entity_{file_index:04}'
        store.add_files([FileRecord(fid, 'repo', generation.generation_id, path, 'a' * 64, 100)])
        entity = Entity(eid, 'repo', fid, generation.generation_id, name,
                        f'module_{file_index:04}.{name}', EntityKind.FUNCTION,
                        (1, 0, chunks_per_file, 4), properties={'rel_path': path})
        entities.append(entity)
        for index in range(chunks_per_file):
            content = f'pass # {file_index} {index}'
            chunks.append(Chunk(f'chunk_{file_index:04}_{index:04}', fid,
                                generation.generation_id, path, (index + 1, 0, index + 1, 4),
                                content, canonical_hash(content), [eid]))
    store.add_entities(entities)
    store.index_chunks(chunks)
    return generation.generation_id, entities, chunks


def test_ranked_exact_names_bound_rows_before_entity_materialization(store, monkeypatch):
    generation, _, _ = populate(store, files=180)
    converted = []
    original = store._row_to_entity
    monkeypatch.setattr(store, '_row_to_entity', lambda row: (converted.append(row['entity_id']), original(row))[1])
    output = HybridRetriever(store, store).retrieve('shared', generation, limit=32, expand_graph=False)
    assert len(output) == 32
    assert len(converted) <= 128, 'SQL name acquisition must not materialize every matching definition'
    assert [row.path for row in output] == [f'module_{i:04}.py' for i in range(32)]


def test_ranked_exact_symbol_does_not_load_whole_file_chunk_lists(store, monkeypatch):
    generation, _, _ = populate(store, chunks_per_file=250)
    acquired = []
    original = store.get_chunks_for_file
    def whole_file(*args, **kwargs):
        rows = original(*args, **kwargs)
        acquired.extend(rows)
        return rows
    monkeypatch.setattr(store, 'get_chunks_for_file', whole_file)
    output = HybridRetriever(store, store).retrieve('shared', generation, limit=32, expand_graph=False)
    assert len(output) == 32
    assert acquired == [], 'Ranked acquisition must use bounded entity/range SQL, not whole-file reads'


def test_bounded_names_keep_non_test_path_preference(store):
    generation, entities, _ = populate(store, files=8)
    for entity in entities[:4]:
        store._execute('UPDATE files SET rel_path = ? WHERE file_id = ?', ('aaa_tests/' + entity.file_id + '.py', entity.file_id))
        store._execute('UPDATE entities SET properties_json = ? WHERE entity_id = ?', ('{}', entity.entity_id))
    rows = store._get_retrieval_entities('shared', generation, limit=3)
    assert [row.entity_id for row in rows] == [row.entity_id for row in entities[4:7]]


def test_qualified_suffix_filters_before_the_acquisition_limit(store):
    generation, entities, _ = populate(store, files=8)
    store._execute('UPDATE entities SET qualified_name = ? WHERE entity_id = ?',
                   ('last.Target.shared', entities[-1].entity_id))
    rows = store._get_retrieval_entities('shared', generation, limit=1, suffix='.Target.shared')
    assert [row.entity_id for row in rows] == [entities[-1].entity_id]


def test_exact_acquisition_budget_counts_duplicate_lookup_rows(store, monkeypatch):
    from codeintel.retrieval import hybrid
    generation, entities, _ = populate(store, files=8)
    monkeypatch.setattr(hybrid, 'MAX_EXACT_ENTITY_ROWS', 3)
    calls = []
    original = store._get_retrieval_entities
    def acquire(*args, **kwargs):
        rows = original(*args, **kwargs)
        calls.extend(rows)
        return rows
    monkeypatch.setattr(store, '_get_retrieval_entities', acquire)
    HybridRetriever(store, store).retrieve('module_0000.shared', generation, expand_graph=False)
    assert len(calls) <= 3


def test_entity_chunk_byte_cap_counts_utf8_before_content_fetch(store, monkeypatch):
    generation, entities, chunks = populate(store, chunks_per_file=4)
    content = '雪山'
    for chunk in chunks:
        store._execute('UPDATE chunks SET content = ?, content_hash = ? WHERE chunk_id = ?',
                       (content, canonical_hash(content), chunk.chunk_id))
    acquired = []
    original = store._row_to_chunk
    monkeypatch.setattr(store, '_row_to_chunk', lambda row: (acquired.append(row['chunk_id']), original(row))[1])
    rows = store._get_retrieval_chunks(entities[0], generation, limit=4, max_bytes=12)
    assert len(rows) == len(acquired) == 2
    assert sum(len(row.content.encode('utf-8')) for row in rows) == 12
    acquired.clear()
    assert store._get_retrieval_chunks(entities[0], generation, limit=4, max_bytes=5) == []
    assert acquired == []
    assert not store._conn.in_transaction


def test_ranked_chunk_budget_is_shared_across_symbols(store, monkeypatch):
    from codeintel.retrieval import hybrid
    generation, _, _ = populate(store, files=8, chunks_per_file=5)
    monkeypatch.setattr(hybrid, 'MAX_ENTITY_CHUNK_ROWS', 7)
    acquired = []
    original = store._row_to_chunk
    monkeypatch.setattr(store, '_row_to_chunk', lambda row: (acquired.append(row['chunk_id']), original(row))[1])
    output = HybridRetriever(store, store).retrieve('shared', generation, limit=32, expand_graph=False)
    assert len(acquired) == 7
    assert len(output) == 5


def test_dotted_query_has_a_fixed_variant_query_budget(store, monkeypatch):
    from codeintel.retrieval import hybrid
    generation, _, _ = populate(store)
    calls = []
    original = store._get_retrieval_entities
    def acquire(name, *args, **kwargs):
        calls.append(name)
        return original(name, *args, **kwargs)
    monkeypatch.setattr(store, '_get_retrieval_entities', acquire)
    HybridRetriever(store, store).retrieve('.'.join(['part'] * 100), generation, expand_graph=False)
    assert len(calls) <= hybrid.MAX_QUALIFIED_VARIANTS + 3


def test_preferred_literal_keeps_exact_owned_chunk_order_and_scope(store):
    generation, entities, chunks = populate(store, files=2, chunks_per_file=4)
    entity = entities[0]
    entity.span = (3, 0, 3, 4)
    rows = store._get_retrieval_chunks(entity, generation, limit=1, max_bytes=1000, preferred_only=True)
    assert [row.chunk_id for row in rows] == [chunks[2].chunk_id]
    assert store._get_retrieval_chunks(entity, 'other_generation', limit=1, max_bytes=1000) == []


@pytest.mark.parametrize('method', ['get_callers', 'get_callees'])
def test_graph_neighbor_lookup_keeps_member_id_sets_inside_sql(store, method):
    from codeintel.core.models import Relation, RelationType
    generation, entities, _ = populate(store, files=80)
    root = entities[0]
    store.add_relations([Relation(f'relation_{i}', 'repo', generation, root.entity_id,
                                   entity.entity_id, RelationType.DEFINES, root.file_id,
                                   (1, 0, 1, 4)) for i, entity in enumerate(entities[1:])])
    member_rows = []
    original = store._row_factory
    def trace(cursor, row):
        value = original(cursor, row)
        if set(value) == {'target_id'}:
            member_rows.append(value)
        return value
    store._conn.setrowtrace(trace)
    assert getattr(store, method)(root.entity_id, generation_id=generation) == []
    assert member_rows == [], 'Graph seeds must remain a SQL set, not an unbounded Python list'
