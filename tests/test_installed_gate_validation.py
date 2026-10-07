"""The install gate must enforce its source contract under -O and -OO too."""
import ast
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from tests.validation_receipt_fixture import write_source_receipt

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/verify_installed_package.py'


@pytest.mark.parametrize('flag', [None, '-O', '-OO'])
@pytest.mark.parametrize('defect', ['hash', 'extra', 'missing', 'private-root', 'broken-link', 'symlink'])
def test_invalid_source_is_rejected_before_python_is_launched(tmp_path, flag, defect):
    source = tmp_path / 'source'
    source.mkdir()
    path = 'input.txt'
    raw = b'approved fixture'
    if defect == 'private-root':
        path = 'agent_exchange/private.txt'
    elif defect == 'broken-link':
        path, raw = 'README.md', b'[missing](missing.md)'
    target = source / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
    files = {path: hashlib.sha256(raw).hexdigest()}
    if defect == 'symlink':
        outside = tmp_path / 'outside.txt'
        outside.write_bytes(raw)
        target.unlink()
        target.symlink_to(outside)
    if defect == 'hash':
        files[path] = '0' * 64
    elif defect == 'extra':
        (source / 'extra.txt').write_bytes(b'unlisted')
    elif defect == 'missing':
        target.unlink()
    (source / 'SNAPSHOT_MANIFEST.json').write_text(json.dumps({'files': files, 'source_commit': 'a' * 40}))
    output = tmp_path / 'output'
    cmd = [sys.executable, *([flag] if flag else []), str(SCRIPT), '--source', str(source), '--python', str(tmp_path / 'missing-python'), '--output', str(output)]
    result = subprocess.run(cmd, capture_output=True, timeout=5)
    assert result.returncode != 0
    assert b'FileNotFoundError' not in result.stderr, result.stderr
    assert b'Source validation failed' in result.stderr
    assert not (output / 'build-source').exists()
    receipt = json.loads((output / 'receipt.json').read_text())
    assert receipt['status'] == 'FAIL' and receipt['failed_stage'] == 'source-validation'


@pytest.mark.parametrize('flag', ['-O', '-OO'])
def test_valid_source_reaches_fresh_environment_probe_under_optimization(tmp_path, flag):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'SNAPSHOT_MANIFEST.json').write_text(json.dumps({'files': {}, 'source_commit': 'a' * 40}))
    receipt = write_source_receipt(tmp_path / 'source-receipt.json')
    result = subprocess.run([sys.executable, flag, str(SCRIPT), '--source', str(source), '--python', str(tmp_path / 'missing-python'), '--output', str(tmp_path / 'output'), '--source-receipt', str(receipt)], capture_output=True, timeout=5)
    assert result.returncode != 0
    assert b'FileNotFoundError' in result.stderr
    assert b'Source validation failed' not in result.stderr


def test_gate_own_checks_are_not_optimization_removable_assertions():
    # Isolated child probes explicitly use -I; inspect the host gate's real AST,
    # not their source-string assertions, which run without inherited optimization.
    assert not any(isinstance(n, ast.Assert) for n in ast.walk(ast.parse(SCRIPT.read_text())))


@pytest.mark.parametrize('defect', ['valid', 'expression', 'license', 'notice', 'private-root', 'artifact', 'extra'])
def test_wheel_license_and_scope_guards_are_preserved(tmp_path, defect):
    import importlib.util
    import zipfile
    spec = importlib.util.spec_from_file_location('installed_gate_guards', SCRIPT)
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'LICENSE').write_bytes(b'authored license')
    (source / 'THIRD_PARTY_NOTICES.md').write_bytes(b'authored notices')
    metadata = 'License-Expression: MIT\n'
    if defect == 'expression':
        metadata = 'License-Expression: Other\n'
    elif defect == 'extra':
        metadata += 'Provides-Extra: unwanted\n'
    files = {
        'codeintel-0.dist-info/METADATA': metadata.encode(),
        'codeintel-0.dist-info/licenses/LICENSE': b'changed' if defect == 'license' else b'authored license',
        'codeintel-0.dist-info/licenses/THIRD_PARTY_NOTICES.md': b'changed' if defect == 'notice' else b'authored notices',
    }
    if defect == 'private-root':
        files['agent_exchange/private.txt'] = b'excluded'
    elif defect == 'artifact':
        files['codeintel/watcher.py'] = b'excluded'
    wheel = tmp_path / 'authored.whl'
    with zipfile.ZipFile(wheel, 'w') as archive:
        for name, raw in files.items():
            archive.writestr(name, raw)
    if defect == 'valid':
        gate.validate_wheel(wheel, source)
    else:
        with pytest.raises(ValueError):
            gate.validate_wheel(wheel, source)


@pytest.mark.parametrize('suffix', ['.py', '.js', '.ts'])
@pytest.mark.parametrize('defect', ['valid', 'changed', 'missing', 'extra', 'duplicate', 'purelib', 'platlib', 'metadata-traversal'])
def test_wheel_runtime_is_exact_before_install(tmp_path, suffix, defect):
    import importlib.util
    import warnings
    import zipfile
    spec = importlib.util.spec_from_file_location('exact_runtime_gate', SCRIPT)
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    source = tmp_path / 'source'
    (source / 'codeintel').mkdir(parents=True)
    (source / 'LICENSE').write_bytes(b'license')
    (source / 'THIRD_PARTY_NOTICES.md').write_bytes(b'notices')
    name = 'codeintel/fixture' + suffix
    (source / name).write_bytes(b'original')
    wheel = tmp_path / 'fixture.whl'
    with zipfile.ZipFile(wheel, 'w') as archive:
        archive.writestr('codeintel-0.dist-info/METADATA', b'License-Expression: MIT\n')
        archive.writestr('codeintel-0.dist-info/licenses/LICENSE', b'license')
        archive.writestr('codeintel-0.dist-info/licenses/THIRD_PARTY_NOTICES.md', b'notices')
        if defect != 'missing':
            archive.writestr(name, b'different' if defect == 'changed' else b'original')
        if defect == 'extra':
            archive.writestr('codeintel/unlisted' + suffix, b'unlisted')
        if defect in ('purelib', 'platlib'):
            archive.writestr('codeintel-0.data/' + defect + '/' + name, b'relocated overwrite')
        if defect == 'metadata-traversal':
            archive.writestr('codeintel-0.dist-info/../' + name, b'traversal overwrite')
        if defect == 'duplicate':
            with warnings.catch_warnings():
                warnings.simplefilter('ignore', UserWarning)
                archive.writestr(name, b'original')
    if defect == 'valid':
        gate.validate_wheel(wheel, source)
    else:
        with pytest.raises(ValueError, match='Wheel (runtime|contains)'):
            gate.validate_wheel(wheel, source)
