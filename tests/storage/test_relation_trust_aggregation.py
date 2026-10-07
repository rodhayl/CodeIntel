"""Neighbor summaries are stable while persisted call-site relations remain distinct."""
import itertools

import pytest

from codeintel.core.models import Entity, EntityKind, Relation, RelationType, TrustClass
from codeintel.storage.sqlite_store import SQLiteStore


def _entities(store, gid, names):
    store.add_entities([
        Entity(name, 'repo', 'file', gid, name, name, EntityKind.FUNCTION, (1, 0, 2, 0))
        for name in names
    ])


def _relations(store, gid, edges):
    store.add_relations([
        Relation(f'r{index}', 'repo', gid, source, target, RelationType.CALLS,
                 'file', (index + 1, 0, index + 1, 1), TrustClass(trust))
        for index, (source, target, trust) in enumerate(edges)
    ])


@pytest.mark.parametrize('trusts', list(itertools.permutations(['EXACT', 'RESOLVED', 'HEURISTIC', 'INFERRED'])))
def test_callsite_order_does_not_change_strongest_neighbor_summary(tmp_path, trusts):
    with SQLiteStore(str(tmp_path / 'db.sqlite')) as store:
        gid = store.create_generation('repo', 1, 'snapshot').generation_id
        _entities(store, gid, ['source', 'target'])
        _relations(store, gid, [('source', 'target', trust) for trust in trusts])
        for method, seed, expected in [
            (store.get_callers, 'target', 'source'),
            (store.get_callees, 'source', 'target'),
        ]:
            rows = method(seed, generation_id=gid, include_heuristic=True)
            assert [(e.entity_id, trust) for _, e, trust in rows] == [(expected, 'EXACT')]
        assert store._execute('SELECT COUNT(*) AS n FROM relations').fetchone()['n'] == 4


@pytest.mark.parametrize('trusts,expected', [
    (['HEURISTIC', 'RESOLVED'], 'RESOLVED'),
    (['RESOLVED', 'HEURISTIC'], 'RESOLVED'),
    (['HEURISTIC', 'INFERRED'], 'HEURISTIC'),
    (['INFERRED'], 'INFERRED'),
])
def test_summary_preserves_available_evidence_class_without_upgrading_it(tmp_path, trusts, expected):
    with SQLiteStore(str(tmp_path / 'db.sqlite')) as store:
        gid = store.create_generation('repo', 1, 'snapshot').generation_id
        _entities(store, gid, ['source', 'target'])
        _relations(store, gid, [('source', 'target', trust) for trust in trusts])
        assert store.get_callees('source', 1, gid, True)[0][2] == expected
        assert store.get_callers('target', 1, gid, True)[0][2] == expected


def test_one_hop_summary_never_includes_transitive_targets(tmp_path):
    with SQLiteStore(str(tmp_path / 'db.sqlite')) as store:
        gid = store.create_generation('repo', 1, 'snapshot').generation_id
        _entities(store, gid, ['source', 'middle', 'target'])
        _relations(store, gid, [('source', 'middle', 'HEURISTIC'), ('middle', 'target', 'RESOLVED')])
        callees = {e.entity_id: trust for _, e, trust in store.get_callees('source', generation_id=gid, include_heuristic=True)}
        callers = {e.entity_id: trust for _, e, trust in store.get_callers('target', generation_id=gid, include_heuristic=True)}
        assert callees == {'middle': 'HEURISTIC'}
        assert callers == {'middle': 'RESOLVED'}
        assert store.get_callees('source', generation_id=gid) == []
        with pytest.raises(ValueError, match='one-hop'):
            store.get_callees('source', max_depth=3, generation_id=gid, include_heuristic=True)
