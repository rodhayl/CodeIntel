"""Expected input boundaries recover without hiding programming errors."""
import json
import os
import shutil
import subprocess
import sys

import pytest

from codeintel.freshness import production
from codeintel.lab import cli
from codeintel.lab.retrieval import index_repository


@pytest.mark.parametrize('command', ['index', 'query'])
@pytest.mark.parametrize('failure,code', [('size', 'SOURCE_TOO_LARGE'), ('type', 'SOURCE_UNAVAILABLE'),
                                         ('git', 'SOURCE_ENUMERATION_FAILED')])
def test_expected_source_failure_has_structured_redacted_cli_recovery(tmp_path, monkeypatch, capsys, command, failure, code):
    repo, state = tmp_path / 'repo', tmp_path / 'state'
    repo.mkdir()
    source = repo / 'a.py'
    source.write_text('secret_source_marker = 12345\n')
    if failure == 'git':
        if shutil.which('git') is None:
            pytest.skip('requires Git')
        subprocess.run([shutil.which('git'), 'init', '-q'], cwd=repo, check=True)
    if command == 'query':
        index_repository(repo, state)
    if failure == 'size':
        monkeypatch.setattr(production, 'MAX_INDEX_SOURCE_FILE_BYTES', 4)
    elif failure == 'type':
        if not hasattr(os, 'mkfifo'):
            pytest.skip('requires FIFO')
        source.unlink()
        os.mkfifo(source)
    else:
        monkeypatch.setattr(production, 'resolve_tool_binary', lambda name: None)
    args = ['codeintel', command, '--repo', str(repo), '--state-dir', str(state)]
    if command == 'query':
        args.append('secret_source_marker')
    monkeypatch.setattr(sys, 'argv', args)
    with pytest.raises(SystemExit) as caught:
        cli.main()
    assert caught.value.code == 2
    output = capsys.readouterr()
    assert output.out == ''
    assert json.loads(output.err)['code'] == code
    assert str(tmp_path) not in output.err
    assert 'secret_source_marker' not in output.err
    assert 'Traceback' not in output.err


def test_unexpected_programming_runtime_error_propagates(tmp_path, monkeypatch):
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / 'a.py').write_text('value = 1\n')
    def broken(*args, **kwargs):
        raise RuntimeError('unexpected programming failure')
    monkeypatch.setattr(production, '_bounded_filesystem_candidate_paths', broken)
    monkeypatch.setattr(sys, 'argv', ['codeintel', 'index', '--repo', str(repo),
                                     '--state-dir', str(tmp_path / 'state')])
    with pytest.raises(RuntimeError, match='unexpected programming failure'):
        cli.main()


@pytest.mark.parametrize('phase', ['ignore', 'enumeration'])
def test_git_stderr_details_never_reach_cli_output(tmp_path, monkeypatch, capsys, phase):
    from types import SimpleNamespace
    from codeintel.freshness import hardened
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / '.git').mkdir()
    secret = f'{tmp_path}/private-source: secret_source_marker'
    failure = SimpleNamespace(timed_out=False, returncode=128, stdout=b'',
                              stderr=secret.encode(), observed_stdout_bytes=0)
    module = hardened if phase == 'ignore' else production
    if phase == 'enumeration':
        monkeypatch.setattr(hardened.HardenedProductionGenerationBarrier,
                            '_compute_ignore_fingerprint', lambda self: 'ignore')
    monkeypatch.setattr(module, 'resolve_tool_binary', lambda name: '/trusted/git')
    monkeypatch.setattr(module, 'run_bounded_process', lambda *args, **kwargs: failure)
    monkeypatch.setattr(sys, 'argv', ['codeintel', 'index', '--repo', str(repo),
                                     '--state-dir', str(tmp_path / 'state')])
    with pytest.raises(SystemExit) as caught:
        cli.main()
    assert caught.value.code == 2
    output = capsys.readouterr()
    assert not output.out
    assert json.loads(output.err)['code'] == 'SOURCE_ENUMERATION_FAILED'
    assert str(tmp_path) not in output.err
    assert 'secret_source_marker' not in output.err


@pytest.mark.skipif(os.name != 'posix', reason='POSIX byte filenames')
@pytest.mark.parametrize('git_repository', [False, True])
@pytest.mark.parametrize('command', ['index', 'query'])
@pytest.mark.parametrize('kind', ['regular', 'symlink'])
def test_non_utf8_filename_has_structured_recovery(tmp_path, monkeypatch, capsys, git_repository, command, kind):
    repo, state = tmp_path / 'repo', tmp_path / 'state'
    repo.mkdir()
    (repo / 'valid.py').write_text('def marker(): return 1\n')
    if git_repository:
        subprocess.run(['git', 'init', '-q'], cwd=repo, check=True)
    if command == 'query':
        index_repository(repo, state)
    filename = os.fsencode(repo) + b'/\xff.py'
    if kind == 'symlink':
        os.symlink(b'valid.py', filename)
    else:
        with open(filename, 'wb') as stream:
            stream.write(b'private_source_marker = 1\n')
    argv = ['codeintel', command, '--repo', str(repo), '--state-dir', str(state)]
    if command == 'query':
        argv.append('marker')
    monkeypatch.setattr(sys, 'argv', argv)
    with pytest.raises(SystemExit) as caught:
        cli.main()
    assert caught.value.code == 2
    output = capsys.readouterr()
    assert not output.out
    assert json.loads(output.err)['code'] == 'SOURCE_UNAVAILABLE'
    assert str(tmp_path) not in output.err and 'private_source_marker' not in output.err
    assert 'Traceback' not in output.err
