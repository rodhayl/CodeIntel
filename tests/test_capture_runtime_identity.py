"""A demo capture cannot attest changed non-Python runtime fixtures."""
import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('suffix', ['.py', '.js', '.ts'])
def test_capture_rejects_changed_runtime_before_demo(tmp_path, monkeypatch, suffix):
    source = tmp_path / 'source'
    runtime = source / 'codeintel'
    runtime.mkdir(parents=True)
    (runtime / '__init__.py').write_text('')
    fixture = runtime / ('fixture' + suffix)
    fixture.write_text('original')
    subprocess.run(['git', 'init', '-q', str(source)], check=True, timeout=5)
    subprocess.run(['git', '-C', str(source), 'add', '.'], check=True, timeout=5)
    subprocess.run(['git', '-C', str(source), '-c', 'user.name=Test',
                    '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'fixture'],
                   check=True, timeout=5)
    fixture.write_text('different')
    spec = importlib.util.spec_from_file_location('capture_identity', ROOT / 'scripts/capture_demo.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'ROOT', source)
    output = tmp_path / 'capture'
    monkeypatch.setattr(sys, 'argv', ['capture_demo', '--output', str(output)])
    with pytest.raises(SystemExit, match='Runtime differs from HEAD'):
        module.main()
    assert not (output / 'capture.json').exists()
    assert not (output / 'demo-stdout.txt').exists()


@pytest.mark.parametrize('suffix', ['.py', '.js', '.ts'])
@pytest.mark.parametrize('defect', ['missing', 'unlisted'])
def test_capture_checks_head_runtime_membership(tmp_path, monkeypatch, suffix, defect):
    source = tmp_path / 'source'
    runtime = source / 'codeintel'
    runtime.mkdir(parents=True)
    (runtime / '__init__.py').write_text('')
    fixture = runtime / ('fixture' + suffix)
    fixture.write_text('original')
    subprocess.run(['git', 'init', '-q', str(source)], check=True, timeout=5)
    subprocess.run(['git', '-C', str(source), 'add', '.'], check=True, timeout=5)
    subprocess.run(['git', '-C', str(source), '-c', 'user.name=Test',
                    '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'fixture'],
                   check=True, timeout=5)
    if defect == 'missing':
        fixture.unlink()
    else:
        (runtime / ('extra' + suffix)).write_text('unlisted')
    spec = importlib.util.spec_from_file_location('capture_membership', ROOT / 'scripts/capture_demo.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'ROOT', source)
    output = tmp_path / 'capture'
    monkeypatch.setattr(sys, 'argv', ['capture_demo', '--output', str(output)])
    with pytest.raises(SystemExit, match='Runtime file set differs from HEAD'):
        module.main()
    assert not (output / 'capture.json').exists()
    assert not (output / 'demo-stdout.txt').exists()
