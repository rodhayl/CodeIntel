"""The advertised offline scope is enforced, including its explicit limitations."""
import copy
import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from codeintel.lab.retrieval import index_repository, query_repository, verify_packet
from codeintel.lab.scenarios import FIXTURE
from codeintel.service import create_default_service


@pytest.mark.parametrize("module", ["codeintel.embeddings", "codeintel.context", "codeintel.gui",
    "codeintel.harness", "codeintel.evaluation", "codeintel.tool_output", "codeintel.interfaces",
    "codeintel.parsing.scip_adapter", "codeintel.service.configured_factory", "codeintel.service.task_context"])
def test_retired_modules_are_absent(module):
    assert importlib.util.find_spec(module) is None


@pytest.mark.parametrize("kwargs", [{"enable_dense": True}, {"enable_scip": True}])
def test_retired_capability_rejected_before_state_creation(tmp_path, kwargs):
    repo = tmp_path / "repo"
    repo.mkdir()
    state = tmp_path / "state"
    with pytest.raises(ValueError, match="not part of the offline"):
        create_default_service(str(repo), str(state), **kwargs)
    assert not state.exists()


def test_verify_does_not_claim_whole_repository_or_empty_completeness(tmp_path):
    repo, state = tmp_path / "repo", tmp_path / "state"
    shutil.copytree(FIXTURE, repo, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    index_repository(repo, state)
    packet, _ = query_repository(repo, state, "reserve_seats")
    empty, _ = query_repository(repo, state, "brand_new_symbol")
    assert empty["status"] == "EMPTY"
    (repo / "new.py").write_text("def brand_new_symbol():\n    return 42\n")
    verify_packet(repo, packet)
    verify_packet(repo, empty)
    fresh, _ = query_repository(repo, state, "brand_new_symbol")
    assert fresh["refreshed"] and fresh["selected"][0]["path"] == "new.py"


def test_duplicate_omission_is_per_packet_not_cross_query_memory(tmp_path):
    repo, state = tmp_path / "repo", tmp_path / "state"
    shutil.copytree(FIXTURE, repo, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    index_repository(repo, state)
    first, a = query_repository(repo, state, "reserve_seats")
    second, b = query_repository(repo, state, "reserve_seats")
    assert first["selected"] == second["selected"]
    assert first["omitted"]["duplicate"] == second["omitted"]["duplicate"] == 1
    assert a["agent_received"] is b["agent_received"] is None
    assert a["tokens"] is b["tokens"] is None


def test_full_rebuild_preserves_unchanged_source_and_fts(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "first.py").write_text("def first():\n    return 'alpha'\n")
    (repo / "second.py").write_text("def second():\n    return 'bravo'\n")
    with create_default_service(str(repo), str(tmp_path / "state")) as service:
        initial = service.reindex()
        (repo / "first.py").write_text("def first():\n    return 'changed'\n")
        fresh = service.reindex()
        assert initial["is_incremental"] is fresh["is_incremental"] is False
        assert initial["generation_id"] != fresh["generation_id"]
        assert any("bravo" in row["source_text"] for row in service.search("second"))
        generation = fresh["generation_id"]
        assert set(service.graph_store.get_all_chunk_ids(generation)) == set(service.graph_store.get_all_fts_chunk_ids(generation))


def test_demo_without_numpy_models_indexers_or_network(tmp_path):
    script = '''
import sys,socket
for name in ('numpy','torch','transformers','llama_cpp','watchdog','google.protobuf'):
 sys.modules[name]=None
def forbidden(*args,**kwargs):raise AssertionError('network was used')
socket.socket.connect=socket.socket.connect_ex=socket.create_connection=forbidden
from pathlib import Path
from codeintel.lab.scenarios import run_demo,evaluate
assert run_demo(Path(sys.argv[1]))['status']=='PASS'
assert evaluate()['status']=='PASS'
'''
    run = subprocess.run([sys.executable, "-c", script, str(tmp_path / "demo")], capture_output=True, timeout=20)
    assert run.returncode == 0, run.stderr


@pytest.mark.parametrize('command', ['index', 'query'])
@pytest.mark.parametrize('indirection', ['direct', 'symlink'])
def test_receipt_cannot_write_inside_inspected_source(tmp_path, command, indirection):
    import json
    repo, state = tmp_path / 'repo', tmp_path / 'state'
    shutil.copytree(FIXTURE, repo, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    if command == 'query':
        index_repository(repo, state)
    before = {p.relative_to(repo).as_posix(): p.read_bytes() for p in repo.rglob('*') if p.is_file()}
    if indirection == 'symlink':
        parent = tmp_path / 'link-to-source'
        parent.symlink_to(repo, target_is_directory=True)
    else:
        parent = repo
    argv = [sys.executable, '-m', 'codeintel.lab.cli', command, '--repo', str(repo),
            '--state-dir', str(state), '--record', str(parent / 'receipt.py')]
    if command == 'query':argv += ['reserve_seats']
    run = subprocess.run(argv, capture_output=True, timeout=20)
    assert run.returncode == 2 and run.stdout == b''
    assert json.loads(run.stderr)['code'] == 'RECORD_INSIDE_REPOSITORY'
    assert {p.relative_to(repo).as_posix(): p.read_bytes() for p in repo.rglob('*') if p.is_file()} == before
    if command == 'index':assert not state.exists()


def test_live_legacy_artifact_cleanup_has_no_vector_dependency(tmp_path):
    from codeintel.freshness.hardened import HardenedProductionGenerationBarrier as ProductionGenerationBarrier
    state = tmp_path / 'state'
    folder = state / '.codeintel_vectors'
    folder.mkdir(parents=True)
    artifact = folder / 'vectors_gen_safe.npz'
    artifact.write_bytes(b'legacy opaque data')
    barrier = object.__new__(ProductionGenerationBarrier)
    barrier.state_dir = str(state)
    barrier._remove_generation_sidecar('gen_safe')
    assert not artifact.exists()
    with pytest.raises(ValueError):barrier._remove_generation_sidecar('../../outside')
    outside = tmp_path / 'outside'
    outside.write_bytes(b'keep')
    artifact.symlink_to(outside)
    with pytest.raises(RuntimeError):barrier._remove_generation_sidecar('gen_safe')
    assert outside.read_bytes() == b'keep'
