"""The standard sdist is build source, not a partial review archive."""
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]


def test_standard_sdist_has_its_own_complete_install_contract(tmp_path):
    build = tmp_path / 'build'
    shutil.copytree(ROOT, build, ignore=shutil.ignore_patterns(
        '.git', '__pycache__', '*.pyc', '.pytest_cache', '*.egg-info', 'build', 'dist'))
    artifacts = tmp_path / 'artifacts'
    artifacts.mkdir()
    result = subprocess.run([sys.executable, '-c',
        'import setuptools.build_meta as b; b.build_sdist(__import__("sys").argv[1])',
        str(artifacts)], cwd=build, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    archive, = artifacts.glob('*.tar.gz')
    extracted = tmp_path / 'extracted'
    with tarfile.open(archive) as tar:
        files = {str(Path(item.name).relative_to(Path(item.name).parts[0]))
                 for item in tar.getmembers() if item.isfile()}
        assert {'README.package.md', 'requirements-lab.lock', 'pyproject.toml',
                'LICENSE', 'THIRD_PARTY_NOTICES.md', 'MANIFEST.in'} <= files
        assert not {'README.md', 'README.es.md', 'requirements-lab-dev.lock'} & files
        assert not any(name.startswith(('tests/', 'scripts/', 'docs/', '.git/'))
                       or name.endswith(('.pyc', '.log', '.jsonl')) for name in files)
        tar.extractall(extracted, filter='data')
    source, = extracted.iterdir()
    readme = (source / 'README.package.md').read_text()
    assert 'build-source' in readme and 'not the review archive' in readme
    assert 'requirements-lab.lock' in readme
    assert 'validate_portfolio.py' not in readme
    assert 'pytest' not in readme
    assert not (source / '.git').exists()
    built = subprocess.run([sys.executable, '-c',
        'import setuptools.build_meta as b; b.build_wheel(__import__("sys").argv[1])',
        str(artifacts)], cwd=source, capture_output=True, timeout=30)
    assert built.returncode == 0, built.stderr
    wheel, = artifacts.glob('*.whl')
    with zipfile.ZipFile(wheel) as package:
        runtime = {str(p.relative_to(ROOT)): p.read_bytes()
                   for p in (ROOT / 'codeintel').rglob('*')
                   if p.is_file() and p.suffix in ('.py', '.js', '.ts')}
        assert len([name for name in runtime if name.endswith('.py')]) == 46
        assert {name for name in package.namelist() if name.startswith('codeintel/')} == set(runtime)
        for name, content in runtime.items():
            assert package.read(name) == content
        metadata = package.read(next(name for name in package.namelist()
                                     if name.endswith('.dist-info/METADATA'))).decode()
        assert readme.strip() in metadata
