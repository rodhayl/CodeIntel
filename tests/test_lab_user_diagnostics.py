"""User-facing cancellation and path advice preserve fail-closed CLI behavior."""
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from codeintel.lab import cli
from codeintel.lab.retrieval import index_repository


@pytest.mark.parametrize('operation', ['index', 'query', 'verify', 'demo', 'evaluate'])
def test_interrupt_is_structured_without_success(monkeypatch, tmp_path, capsys, operation):
    repo = tmp_path / 'repo'
    repo.mkdir()
    record = tmp_path / 'receipt.json'
    def interrupted(*a, **kw):
        raise KeyboardInterrupt
    target = {'index': 'index_repository', 'query': 'query_repository',
              'verify': 'read_regular_file_bounded', 'demo': 'run_demo', 'evaluate': 'evaluate'}[operation]
    monkeypatch.setattr(cli, target, interrupted)
    if operation in ('index', 'query'):
        argv = [operation, '--repo', str(repo), '--state-dir', str(tmp_path / 'state'), '--record', str(record)]
        if operation == 'query':
            argv.append('private_marker')
    elif operation == 'verify':
        argv = [operation, '--repo', str(repo), '--packet', str(tmp_path / 'packet.json')]
    elif operation == 'demo':
        argv = [operation, '--workspace', str(tmp_path / 'demo')]
    else:
        argv = [operation]
    with pytest.raises(SystemExit) as caught:
        cli.main(argv)
    assert caught.value.code == 130
    captured = capsys.readouterr()
    assert captured.out == ''
    error = json.loads(captured.err)
    assert error['code'] == 'CANCELLED'
    assert 'Traceback' not in captured.err and str(tmp_path) not in captured.err
    assert not record.exists()


@pytest.mark.skipif(os.name != 'posix', reason='POSIX filename and symlink policy')
@pytest.mark.parametrize('git_repo', [False, True])
@pytest.mark.parametrize('operation', ['index', 'query'])
@pytest.mark.parametrize('kind', ['backslash', 'symlink'])
def test_rejected_path_advice_is_specific_and_recoverable(tmp_path, capsys, git_repo, operation, kind):
    repo, state = tmp_path / 'repo', tmp_path / 'state'
    repo.mkdir()
    source = repo / 'sample.py'
    source.write_text('def private_marker(): return 42\n')
    if git_repo:
        subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    if operation == 'query':
        index_repository(repo, state)
    bad = repo / ('private\\name.py' if kind == 'backslash' else 'link.py')
    if kind == 'backslash':
        bad.write_text('secret_source = 123\n')
    else:
        bad.symlink_to(source.name)
    record = tmp_path / 'receipt.json'
    argv = [operation, '--repo', str(repo), '--state-dir', str(state), '--record', str(record)]
    if operation == 'query':
        argv.append('private_marker')
    with pytest.raises(SystemExit) as caught:
        cli.main(argv)
    assert caught.value.code == 2
    output = capsys.readouterr()
    assert output.out == '' and not record.exists()
    error = json.loads(output.err)
    assert error['code'] == 'SOURCE_UNAVAILABLE'
    assert ('backslash' in error['recovery']) == (kind == 'backslash')
    assert ('symlink' in error['recovery']) == (kind == 'symlink')
    assert str(tmp_path) not in output.err and 'private_marker' not in output.err
    bad.unlink()
    cli.main(argv)
    assert json.loads(capsys.readouterr().out)['status'] in ('SUCCESS', 'OK')
    assert record.exists()


@pytest.mark.skipif(os.name != 'posix', reason='POSIX SIGINT')
def test_real_sigint_returns_130_without_traceback(tmp_path):
    ready = tmp_path / 'ready'
    script = '''
import sys, time
from pathlib import Path
from codeintel.lab import cli
def waiting(*args, **kwargs):
    Path(sys.argv[1]).write_text('ready')
    while True: time.sleep(0.01)
cli.index_repository = waiting
cli.main(['index', '--repo', sys.argv[2], '--state-dir', sys.argv[3]])
'''
    child = subprocess.Popen([sys.executable, '-c', script, str(ready), str(tmp_path), str(tmp_path / 'state')], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists()
        child.send_signal(signal.SIGINT)
        out, err = child.communicate(timeout=5)
        assert child.returncode == 130
        assert not out and b'Traceback' not in err
        assert json.loads(err)['code'] == 'CANCELLED'
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=5)
