"""Installed acceptance must bind the source and runtime actually tested."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.validation_receipt_fixture import write_source_receipt

ROOT = Path(__file__).resolve().parents[1]


def load_gate():
    spec = importlib.util.spec_from_file_location('installed_identity_gate', ROOT / 'scripts/verify_installed_package.py')
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    return gate


@pytest.mark.parametrize('defect', ['none', 'test-bytes', 'extra-source', 'missing-source', 'manifest',
                                    'runtime-bytes', 'extra-runtime', 'missing-runtime',
                                    'generated-cache', 'cache-symlink', 'unlisted-cache'])
def test_gate_revalidates_actual_source_and_installed_runtime_before_pass(tmp_path, monkeypatch, defect):
    gate = load_gate()
    source = tmp_path / 'source'
    files = {
        'codeintel/__init__.py': b'__version__ = "fixture"\n',
        'codeintel/lab/fixture/display.js': b'function display() {}\n',
        'tests/test_authored_fixture.py': b'def test_fixture():\n    assert 1 == 1\n',
        'README.md': b'Reviewed source.\n',
        'requirements-lab.lock': b'', 'requirements-lab-dev.lock': b'',
        'docs/portfolio/DEMO.txt': b'demo\n', 'docs/portfolio/EVALUATION.txt': b'evaluation\n',
    }
    for name, raw in files.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    hashes = {name: hashlib.sha256(raw).hexdigest() for name, raw in files.items()}
    manifest_path = source / 'SNAPSHOT_MANIFEST.json'
    manifest_path.write_text(json.dumps({'source_commit': 'a' * 40, 'files': hashes}))
    source_receipt_path = write_source_receipt(tmp_path / 'source-receipt.json', hashes)
    source_receipt = json.loads(source_receipt_path.read_text())
    runtime = tmp_path / 'installed' / 'codeintel'
    for name, raw in files.items():
        if name.startswith('codeintel/'):
            path = runtime.parent / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
    out = tmp_path / 'output'

    def run(runner, name, argv, timeout=25):
        if name == 'build-wheel':
            (out / 'wheels').mkdir()
            (out / 'wheels/codeintel-fixture.whl').write_bytes(b'fixture wheel')
        if name == 'installed-probe':
            return SimpleNamespace(stdout=json.dumps({'runtime_root': str(runtime), 'versions': {}}).encode())
        if name.startswith('demo-'):
            return SimpleNamespace(stdout=b'{"status":"PASS"}' if name.endswith('json') else b'demo\n')
        if name.startswith('evaluation-'):
            return SimpleNamespace(stdout=b'{"status":"PASS","rows":[],"checks":[]}' if name.endswith('json') else b'evaluation\n')
        if name == 'claims-from-extracted-source':
            claims = out / 'claim-reproduction'
            claims.mkdir()
            (claims / 'claims.json').write_text(json.dumps({'status': 'PASS', 'source_commit': 'a' * 40}))
            if defect == 'test-bytes':
                (source / 'tests/test_authored_fixture.py').write_text('def test_fixture():\n    assert True\n')
            elif defect == 'extra-source':
                (source / 'unreviewed.txt').write_text('unreviewed')
            elif defect == 'missing-source':
                (source / 'README.md').unlink()
            elif defect == 'manifest':
                manifest_path.write_bytes(manifest_path.read_bytes() + b'\n')
            elif defect == 'runtime-bytes':
                (runtime / '__init__.py').write_text('different runtime\n')
            elif defect == 'extra-runtime':
                (runtime / 'unreviewed.py').write_text('unreviewed')
            elif defect == 'missing-runtime':
                (runtime / 'lab/fixture/display.js').unlink()
            elif defect in ('generated-cache', 'cache-symlink', 'unlisted-cache'):
                name = 'unlisted.py' if defect == 'unlisted-cache' else '__init__.py'
                cache = Path(importlib.util.cache_from_source(str(source / 'codeintel' / name)))
                cache.parent.mkdir()
                if defect == 'cache-symlink':
                    cache.symlink_to(source / 'README.md')
                else:
                    cache.write_bytes(b'generated validation bytecode')
        return SimpleNamespace(stdout=b'')

    report = deepcopy(source_receipt)
    for key in ('collection', 'execution_collection'):
        report[key]['runtime_origins'] = {'codeintel': {'path': '__init__.py', 'sha256': hashes['codeintel/__init__.py']}}
    monkeypatch.setattr(gate.contract.StageRunner, 'run', run)
    monkeypatch.setattr(gate.contract, 'run_pytest_suite', lambda *args, **kwargs: report)
    monkeypatch.setattr(gate, 'validate_wheel', lambda *args: None)
    command = ['--source', str(source), '--python', str(tmp_path / 'venv/bin/python'),
               '--output', str(out), '--source-receipt', str(source_receipt_path)]
    if defect in ('none', 'generated-cache'):
        gate.main(command)
        receipt = json.loads((out / 'receipt.json').read_text())
        assert receipt['status'] == 'PASS'
        assert receipt['source_revalidated_after_execution'] is True
        assert receipt['installed_runtime_revalidated_after_execution'] is True
        assert receipt['runtime_origin_hashes_equal'] is True
    else:
        with pytest.raises(ValueError, match='(Source|Installed runtime)'):
            gate.main(command)
        receipt = json.loads((out / 'receipt.json').read_text())
        assert receipt['status'] == 'FAIL'
        assert receipt['failed_stage'] == 'final-source-runtime-validation'


@pytest.mark.parametrize('phase', ['collection', 'execution_collection'])
@pytest.mark.parametrize('defect', ['changed', 'unlisted', 'missing'])
def test_observed_runtime_origin_hashes_are_compared_with_accepted_bytes(phase, defect):
    gate = load_gate()
    expected = {'codeintel/__init__.py': 'a' * 64}
    report = {key: {'runtime_origins': {'codeintel': {'path': '__init__.py', 'sha256': 'a' * 64}}}
              for key in ('collection', 'execution_collection')}
    gate.validate_runtime_origins(report, expected)
    if defect == 'changed':
        report[phase]['runtime_origins']['codeintel']['sha256'] = 'b' * 64
    elif defect == 'unlisted':
        report[phase]['runtime_origins']['codeintel']['path'] = 'unlisted.py'
    else:
        report[phase]['runtime_origins'] = {}
    with pytest.raises(ValueError, match='Installed runtime origin'):
        gate.validate_runtime_origins(report, expected)


@pytest.mark.parametrize('suffix', ['.py', '.js', '.ts'])
def test_installed_disk_parity_covers_every_runtime_language(tmp_path, suffix):
    gate = load_gate()
    runtime = tmp_path / 'codeintel'
    runtime.mkdir()
    path = runtime / ('fixture' + suffix)
    path.write_bytes(b'original')
    expected = {'codeintel/' + path.name: hashlib.sha256(b'original').hexdigest()}
    gate.validate_installed_runtime(runtime, expected)
    path.write_bytes(b'different')
    with pytest.raises(ValueError, match='Installed runtime file set or bytes differ'):
        gate.validate_installed_runtime(runtime, expected)


def test_pytest_cache_destination_is_outside_the_tested_source():
    launcher = load_gate().contract.PYTEST_LAUNCHER
    assert "cache_dir=" in launcher
    assert "Path(output).parent/'pytest-cache'" in launcher
