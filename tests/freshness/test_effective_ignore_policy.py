"""Fingerprinting and snapshots follow the scanner's effective Git ignore policy."""
import os
import shutil
import subprocess

import pytest

from codeintel.freshness.hardened import HardenedProductionGenerationBarrier
from codeintel.service import create_default_service


def _git(repo, *args):
    subprocess.run([shutil.which('git'), *args], cwd=repo, check=True, capture_output=True)


@pytest.mark.skipif(shutil.which('git') is None, reason='requires Git')
@pytest.mark.parametrize('external_kind', ['regular', 'fifo'])
def test_external_excludes_is_neither_read_nor_fingerprinted(tmp_path, external_kind):
    repo = tmp_path / 'repo'
    repo.mkdir()
    _git(repo, 'init', '-q')
    (repo / 'a.py').write_text('value = 1\n')
    external = tmp_path / 'external.ignore'
    if external_kind == 'fifo':
        if not hasattr(os, 'mkfifo'):
            pytest.skip('requires FIFO')
        os.mkfifo(external)
    else:
        external.write_text('a.py\n')
    _git(repo, 'config', 'core.excludesfile', str(external))
    with create_default_service(str(repo), str(tmp_path / 'state')) as service:
        first = service.reindex()
        fingerprint = service.barrier._compute_ignore_fingerprint()
        assert service.barrier.file_hashes.keys() == {'a.py'}
        if external_kind == 'regular':
            external.write_text('other.py\n')
        assert service.barrier._compute_ignore_fingerprint() == fingerprint
        assert service.reindex()['generation_id'] == first['generation_id']


@pytest.mark.skipif(shutil.which('git') is None, reason='requires Git')
@pytest.mark.parametrize('ignore_path', ['.gitignore', '.git/info/exclude', 'pkg/.gitignore'])
def test_effective_ignore_changes_refresh_candidates_in_both_directions(tmp_path, ignore_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    _git(repo, 'init', '-q')
    (repo / 'pkg').mkdir()
    (repo / 'pkg/a.py').write_text('value = 1\n')
    with create_default_service(str(repo), str(tmp_path / 'state')) as service:
        first = service.reindex()
        ignore = repo / ignore_path
        ignore.write_text('a.py\n')
        assert service.barrier.check_freshness()[0] is False
        second = service.reindex()
        assert second['generation_id'] != first['generation_id']
        assert 'pkg/a.py' not in service.barrier.file_hashes
        ignore.unlink()
        assert service.barrier.check_freshness()[0] is False
        service.reindex()
        assert 'pkg/a.py' in service.barrier.file_hashes
