from __future__ import annotations

import pytest
from codeintel.storage.generation_metadata import load_generation_metadata


def test_conflicting_persisted_build_fingerprints_fail_closed():
    with pytest.raises(RuntimeError, match='strict JSON'):
        load_generation_metadata(
            '{"build_fingerprint":"metadata-fp"}',
            build_fingerprint='column-fp',
        )


def test_matching_persisted_build_fingerprint_is_preserved():
    value = load_generation_metadata(
        '{"build_fingerprint":"same","lifecycle_state":"COMMITTED"}',
        build_fingerprint='same',
    )
    assert value['build_fingerprint'] == 'same'


def test_missing_metadata_build_fingerprint_is_backfilled_from_column():
    value = load_generation_metadata('{"lifecycle_state":"COMMITTED"}', build_fingerprint='column-fp')
    assert value['build_fingerprint'] == 'column-fp'


@pytest.mark.parametrize('bad', [123, True, False, '', [], {'key': 'value'}])
def test_invalid_fingerprint_update_cannot_poison_persisted_generation(tmp_path, bad):
    from codeintel.storage.production import ProductionSQLiteStore
    with ProductionSQLiteStore(str(tmp_path / 'db.sqlite')) as store:
        generation = store.create_generation('repo', 1, 'a' * 64, build_fingerprint='original')
        before = dict(store._execute('SELECT * FROM generations WHERE generation_id=?',
                                     (generation.generation_id,)).fetchone())
        with pytest.raises(ValueError, match='build_fingerprint'):
            store.update_generation_metadata(generation.generation_id, {'build_fingerprint': bad})
        after = dict(store._execute('SELECT * FROM generations WHERE generation_id=?',
                                    (generation.generation_id,)).fetchone())
        assert after == before
        assert not store._conn.in_transaction
        assert store.list_generations('repo')[0].build_fingerprint == 'original'
        store.update_generation_metadata(generation.generation_id, {'build_fingerprint': 'next'})
        assert store.list_generations('repo')[0].build_fingerprint == 'next'


def test_none_fingerprint_update_preserves_existing_backfill_contract(tmp_path):
    from codeintel.storage.production import ProductionSQLiteStore
    with ProductionSQLiteStore(str(tmp_path / 'db.sqlite')) as store:
        generation = store.create_generation('repo', 1, 'a' * 64, build_fingerprint='original')
        store.update_generation_metadata(generation.generation_id, {'build_fingerprint': None})
        assert store.list_generations('repo')[0].build_fingerprint == 'original'
