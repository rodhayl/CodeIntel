"""A required receipt must finish before any success bytes leave the CLI."""
import errno
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace

import pytest

from codeintel.lab import cli


def _stub_query(monkeypatch, tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    record = tmp_path / 'receipt.json'
    monkeypatch.setattr(cli, 'query_repository', lambda *a, **kw: (
        {'status': 'OK'}, {'operation': 'query', 'agent_received': None}))
    return record, ['query', '--repo', str(repo), '--state-dir', str(tmp_path / 'state'),
                    'marker', '--record', str(record)]


def test_required_receipt_finishes_before_stdout(monkeypatch, tmp_path):
    record, argv = _stub_query(monkeypatch, tmp_path)
    class ObserveOutput(io.BytesIO):
        def write(self, data):
            receipt = json.loads(record.read_bytes())
            assert receipt['delivery'] == 'prepared_for_stdout'
            assert receipt['agent_received'] is None
            return super().write(data)
    output = ObserveOutput()
    monkeypatch.setattr(cli.sys, 'stdout', SimpleNamespace(buffer=output))
    cli.main(argv)
    assert json.loads(output.getvalue())['status'] == 'OK'


@pytest.mark.parametrize('operation', ['index', 'query'])
def test_real_denied_receipt_permission_emits_no_stdout(operation):
    # A real kernel permission failure, not a mock. The child drops privilege
    # only when this test is run as root; all changed paths are disposable.
    with tempfile.TemporaryDirectory(prefix='codeintel-denied-receipt-') as folder:
        root = Path(folder)
        root.chmod(0o777)
        repo = root / 'repo'
        repo.mkdir(mode=0o755)
        (repo / 'sample.py').write_text('VALUE = "marker"\n')
        denied = root / 'denied'
        denied.mkdir(mode=0o555)
        record = denied / 'receipt.json'
        script = '''
import os, sys
from codeintel.lab.cli import main
if os.geteuid() == 0:
    os.setgid(65534)
    os.setuid(65534)
main(sys.argv[1:])
'''
        common = ['--repo', str(repo), '--state-dir', str(root / 'state')]
        indexed = subprocess.run([sys.executable, '-c', script, 'index', *common],
                                 capture_output=True, timeout=20)
        assert indexed.returncode == 0, indexed.stderr
        try:
            command = [sys.executable, '-c', script, operation, *common, '--record', str(record)]
            if operation == 'query':
                command.append('marker')
            result = subprocess.run(command,
                                    capture_output=True, timeout=20)
            assert result.returncode == 2, result.stderr
            assert result.stdout == b''
            assert json.loads(result.stderr)['code'] == 'PermissionError'
            assert not record.exists()
        finally:
            denied.chmod(0o755)


@pytest.mark.parametrize('stage', ['open', 'write', 'flush', 'fsync', 'close'])
def test_receipt_io_failure_prevents_stdout(monkeypatch, tmp_path, capsys, stage):
    record, argv = _stub_query(monkeypatch, tmp_path)
    def fail(*args, **kwargs):
        raise OSError(errno.ENOSPC, 'injected receipt failure')
    if stage == 'open':
        original = os.open
        def open_file(path, flags, *args, **kwargs):
            return fail() if flags & os.O_EXCL else original(path, flags, *args, **kwargs)
        monkeypatch.setattr(cli.os, 'open', open_file)
    elif stage == 'fsync':
        monkeypatch.setattr(cli.os, 'fsync', fail)
    else:
        original = os.fdopen
        class FailingReceipt:
            def __init__(self, fd, mode):
                self.inner = original(fd, mode)
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.inner.close()
                if stage == 'close':
                    fail()
            def write(self, data):
                return fail() if stage == 'write' else self.inner.write(data)
            def flush(self):
                return fail() if stage == 'flush' else self.inner.flush()
            def fileno(self):
                return self.inner.fileno()
        monkeypatch.setattr(cli.os, 'fdopen', FailingReceipt)
    with pytest.raises(SystemExit) as caught:
        cli.main(argv)
    assert caught.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ''
    assert json.loads(captured.err)['code'] == 'OSError'


@pytest.mark.parametrize('stage', ['write', 'flush'])
def test_stdout_failure_keeps_truthful_prepared_receipt(monkeypatch, tmp_path, capsys, stage):
    record, argv = _stub_query(monkeypatch, tmp_path)
    class FailedOutput(io.BytesIO):
        def write(self, data):
            if stage == 'write':
                raise BrokenPipeError('injected stdout failure')
            return super().write(data)
        def flush(self):
            if stage == 'flush':
                raise BrokenPipeError('injected stdout failure')
    output = FailedOutput()
    monkeypatch.setattr(cli.sys, 'stdout', SimpleNamespace(buffer=output))
    with pytest.raises(SystemExit) as caught:
        cli.main(argv)
    assert caught.value.code == 2
    assert json.loads(record.read_bytes())['delivery'] == 'prepared_for_stdout'
    assert json.loads(capsys.readouterr().err)['code'] == 'BrokenPipeError'


def test_receipt_parent_is_pinned_across_symlink_swap(monkeypatch, tmp_path, capsys):
    record, argv = _stub_query(monkeypatch, tmp_path)
    parent = tmp_path / 'receipts'
    parent.mkdir()
    pinned = tmp_path / 'original'
    outside = tmp_path / 'outside'
    outside.mkdir()
    argv[-1] = str(parent / record.name)
    def query(*args, **kwargs):
        parent.rename(pinned)
        parent.symlink_to(outside, target_is_directory=True)
        return {'status': 'OK'}, {'operation': 'query'}
    monkeypatch.setattr(cli, 'query_repository', query)
    cli.main(argv)
    assert json.loads(capsys.readouterr().out)['status'] == 'OK'
    assert json.loads((pinned / record.name).read_bytes())['delivery'] == 'prepared_for_stdout'
    assert not (outside / record.name).exists()


@pytest.mark.parametrize('kind', ['regular', 'symlink', 'fifo'])
def test_receipt_created_after_preflight_is_never_overwritten(monkeypatch, tmp_path, capsys, kind):
    record, argv = _stub_query(monkeypatch, tmp_path)
    outside = tmp_path / 'outside.json'
    outside.write_bytes(b'keep outside')
    def query(*args, **kwargs):
        if kind == 'regular':
            record.write_bytes(b'keep existing')
        elif kind == 'symlink':
            record.symlink_to(outside)
        else:
            os.mkfifo(record)
        return {'status': 'OK'}, {'operation': 'query'}
    monkeypatch.setattr(cli, 'query_repository', query)
    with pytest.raises(SystemExit) as caught:
        cli.main(argv)
    assert caught.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ''
    assert json.loads(captured.err)['code'] == 'FileExistsError'
    assert outside.read_bytes() == b'keep outside'
    if kind == 'regular':
        assert record.read_bytes() == b'keep existing'


def test_receipt_fsync_cancellation_closes_descriptors_without_stdout(monkeypatch, tmp_path, capsys):
    record, argv = _stub_query(monkeypatch, tmp_path)
    opened = []
    original = os.open
    def track(*args, **kwargs):
        fd = original(*args, **kwargs)
        opened.append(fd)
        return fd
    def cancel(fd):
        raise KeyboardInterrupt('cancel receipt sync')
    monkeypatch.setattr(cli.os, 'open', track)
    monkeypatch.setattr(cli.os, 'fsync', cancel)
    with pytest.raises(SystemExit) as caught:
        cli.main(argv)
    assert caught.value.code == 130
    captured = capsys.readouterr()
    assert captured.out == ''
    assert json.loads(captured.err)['code'] == 'CANCELLED'
    for fd in set(opened):
        with pytest.raises(OSError):
            os.fstat(fd)
    assert json.loads(record.read_bytes())['delivery'] == 'prepared_for_stdout'


def test_receipt_fdopen_failure_closes_unowned_descriptor(monkeypatch, tmp_path, capsys):
    record, argv = _stub_query(monkeypatch, tmp_path)
    opened = []
    def fail(fd, mode):
        opened.append(fd)
        raise OSError('injected fdopen failure')
    monkeypatch.setattr(cli.os, 'fdopen', fail)
    with pytest.raises(SystemExit) as caught:
        cli.main(argv)
    assert caught.value.code == 2
    assert capsys.readouterr().out == ''
    assert len(opened) == 1
    with pytest.raises(OSError):
        os.fstat(opened[0])


def test_literal_cli_reuses_packet_contract_and_truthful_telemetry(tmp_path):
    from codeintel.lab.retrieval import index_repository, query_repository, serialize
    repo, state = tmp_path / 'repo', tmp_path / 'state'
    repo.mkdir()
    (repo / 'example.py').write_text('XY = "id"\nNOTE = "雪山"\n', encoding='utf-8')
    index_repository(repo, state)
    for literal in [False, True]:
        receipt = tmp_path / f'receipt-{literal}.json'
        command = [sys.executable, '-m', 'codeintel.lab.cli', 'query', '--repo', str(repo),
                   '--state-dir', str(state), '雪山', '--record', str(receipt)]
        if literal:
            command.append('--literal')
        result = subprocess.run(command, capture_output=True, timeout=20)
        assert result.returncode == 0, result.stderr
        expected, _ = query_repository(repo, state, '雪山', baseline=literal)
        assert result.stdout == serialize(expected)
        metadata = json.loads(receipt.read_bytes())
        assert metadata['config']['retrieval'] == ('literal_scan' if literal else 'ranked')
        assert metadata['delivery'] == 'prepared_for_stdout'
        assert metadata['agent_received'] is None
        assert '雪山' not in receipt.read_text()
    help_result = subprocess.run([sys.executable, '-m', 'codeintel.lab.cli', 'query', '--help'],
                                 capture_output=True, timeout=20)
    assert help_result.returncode == 0 and b'--literal' in help_result.stdout


@pytest.mark.parametrize('destination', ['receipt', 'stdout'])
def test_short_write_reports_failure(monkeypatch, tmp_path, capsys, destination):
    record, argv = _stub_query(monkeypatch, tmp_path)
    if destination == 'stdout':
        class ShortOutput(io.BytesIO):
            def write(self, data):
                return super().write(data[:1])
        monkeypatch.setattr(cli.sys, 'stdout', SimpleNamespace(buffer=ShortOutput()))
    else:
        original = os.fdopen
        class ShortReceipt:
            def __init__(self, fd, mode):
                self.inner = original(fd, mode)
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.inner.close()
            def write(self, data):
                return self.inner.write(data[:1])
        monkeypatch.setattr(cli.os, 'fdopen', ShortReceipt)
    with pytest.raises(SystemExit) as caught:
        cli.main(argv)
    assert caught.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ''
    assert json.loads(captured.err)['code'] == 'OSError'
    if destination == 'receipt':
        assert record.read_bytes() == b'{'
    else:
        assert json.loads(record.read_bytes())['delivery'] == 'prepared_for_stdout'
