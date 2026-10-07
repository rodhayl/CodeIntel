"""The maintained factory resolves every safety boundary to one live implementation."""
from codeintel.freshness.barrier import GenerationBarrier
from codeintel.freshness.production import ProductionGenerationBarrier
from codeintel.freshness.hardened import HardenedProductionGenerationBarrier
from codeintel.service import create_default_service


def test_live_factory_method_resolution_has_no_shadow_safety_bodies(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    expected = {
        '__init__': GenerationBarrier,
        '_stable_snapshot': GenerationBarrier,
        'release_rebuild_lock': GenerationBarrier,
        '_get_git_head': ProductionGenerationBarrier,
        'scan_worktree': ProductionGenerationBarrier,
        'check_freshness': ProductionGenerationBarrier,
        '_validate_offline_artifacts': ProductionGenerationBarrier,
        '_compute_ignore_fingerprint': HardenedProductionGenerationBarrier,
        'acquire_rebuild_lock': HardenedProductionGenerationBarrier,
        '_switch_generation_lease': HardenedProductionGenerationBarrier,
        '_remove_generation_sidecar': HardenedProductionGenerationBarrier,
        '_gc_generations': HardenedProductionGenerationBarrier,
        'abort_staging_generation': HardenedProductionGenerationBarrier,
    }
    with create_default_service(str(repo), str(tmp_path / 'state')) as service:
        barrier = service.barrier
        for name, owner in expected.items():
            definitions = [cls for cls in type(barrier).__mro__ if cls is not object and name in cls.__dict__]
            assert definitions == [owner], (name, definitions)
        assert not hasattr(barrier, 'file_mtimes')
        assert not hasattr(barrier, 'configure_dense_precommit_validation')
        assert not hasattr(barrier, '_git_has_no_relevant_dirty_paths')


def test_live_sidecar_guard_repairs_through_new_immutable_generation(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / 'a.py').write_text('value = 1\n')
    state = tmp_path / 'state'
    with create_default_service(str(repo), str(state)) as service:
        first = service.reindex()
        folder = state / '.codeintel_vectors'
        folder.mkdir(exist_ok=True)
        legacy = folder / f"vectors_{first['generation_id']}.npz"
        legacy.write_bytes(b'opaque historical sidecar')
        second = service.reindex()
        assert second['generation_id'] != first['generation_id']
        active = service.barrier.active_generation
        assert active.metadata['artifact_repair_required'] is True
        assert active.metadata['artifact_repair_source_generation'] == first['generation_id']
        assert not (folder / f"vectors_{second['generation_id']}.npz").exists()
