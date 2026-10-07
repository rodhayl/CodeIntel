"""Claim reproduction must work from an extracted archive, even inside another repo."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('with_manifest', [False, True])
def test_claim_runner_without_git_does_not_inherit_parent_repository(tmp_path, with_manifest):
    parent = tmp_path / 'unrelated'
    parent.mkdir()
    subprocess.run(['git', 'init', '-q', str(parent)], check=True, timeout=5)
    source = parent / 'extracted-codeintel'
    source.mkdir()
    shutil.copytree(ROOT / 'codeintel', source / 'codeintel', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    (source / 'scripts').mkdir()
    shutil.copyfile(ROOT / 'scripts/reproduce_claims.py', source / 'scripts/reproduce_claims.py')
    shutil.copyfile(ROOT / 'scripts/reproduce_use_cases.py', source / 'scripts/reproduce_use_cases.py')
    (source / 'docs/portfolio').mkdir(parents=True)
    shutil.copyfile(ROOT / 'docs/portfolio/HISTORICAL_EVIDENCE.json', source / 'docs/portfolio/HISTORICAL_EVIDENCE.json')
    historical = Path('docs/reports/portfolio-readiness/public/historical-measurements.json')
    (source / historical).parent.mkdir(parents=True)
    shutil.copyfile(ROOT / historical, source / historical)
    if with_manifest:
        files = {p.relative_to(source).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in (source / 'codeintel').rglob('*')
                 if p.is_file() and p.suffix in ('.py', '.js', '.ts')}
        (source / 'SNAPSHOT_MANIFEST.json').write_text(json.dumps({'source_commit': 'a' * 40, 'files': files}))
    output = tmp_path / 'evidence'
    run = subprocess.run([sys.executable, str(source / 'scripts/reproduce_claims.py'), '--output', str(output)],
                         cwd=tmp_path, capture_output=True, timeout=20)
    assert run.returncode == 0, run.stderr
    report = json.loads((output / 'claims.json').read_text())
    assert report['status'] == 'PASS'
    assert report['source_commit'] == ('a' * 40 if with_manifest else None)
    assert report['source_dirty'] is (False if with_manifest else None)
    assert report['runtime_sha256']


@pytest.mark.parametrize('suffix', ['.py', '.js', '.ts'])
@pytest.mark.parametrize('defect', ['changed', 'missing', 'unlisted'])
def test_manifest_identity_covers_every_shipped_runtime_language(tmp_path, suffix, defect):
    import importlib.util
    spec = importlib.util.spec_from_file_location('claim_identity', ROOT / 'scripts/reproduce_claims.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    runtime = tmp_path / 'codeintel'
    runtime.mkdir()
    (runtime / '__init__.py').write_text('')
    fixture = runtime / ('fixture' + suffix)
    fixture.write_text('original')
    files = {p.relative_to(tmp_path).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in runtime.iterdir()}
    (tmp_path / 'SNAPSHOT_MANIFEST.json').write_text(json.dumps({'files': files, 'source_commit': 'a' * 40}))
    valid = module.source_identity(tmp_path)
    assert valid['source_dirty'] is False and valid['source_commit'] == 'a' * 40
    assert set(valid['runtime_sha256']) == {name for name in files if name.endswith('.py')}
    if defect == 'changed':
        fixture.write_text('different')
    elif defect == 'missing':
        fixture.unlink()
    else:
        (runtime / ('extra' + suffix)).write_text('unlisted')
    invalid = module.source_identity(tmp_path)
    assert invalid['source_dirty'] is True and invalid['source_commit'] is None
    assert set(valid['runtime_files_sha256']) == set(files)


@pytest.mark.parametrize('script', ['reproduce_claims.py', 'reproduce_use_cases.py', 'review_retrieval_cases.py'])
def test_imported_reproduction_helpers_do_not_change_runtime_search_path(script):
    import importlib.util
    before = list(sys.path)
    try:
        spec = importlib.util.spec_from_file_location('isolated_reproduction_helper', ROOT / 'scripts' / script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert sys.path == before
    finally:
        # A failing baseline must not contaminate the remaining suite.
        sys.path[:] = before
