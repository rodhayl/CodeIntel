"""Real buffered stdout failures must survive interpreter shutdown cleanly."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize('sink', ['full', 'closed-pipe'])
@pytest.mark.parametrize('operation', ['index', 'query'])
def test_real_stdout_failure_exits_two_without_finalizer_noise(tmp_path, sink, operation):
    repo, state = tmp_path / 'repo', tmp_path / 'state'
    repo.mkdir()
    (repo / 'source.py').write_text('def unicode_marker():\n    return "雪山"\n')
    base = [sys.executable, '-m', 'codeintel.lab.cli']
    args = ['--repo', str(repo), '--state-dir', str(state)]
    indexed = subprocess.run([*base, 'index', *args], capture_output=True, timeout=20)
    assert indexed.returncode == 0, indexed.stderr
    receipt = tmp_path / 'receipt.json'
    command = [*base, operation, *args, '--record', str(receipt)]
    if operation == 'query':
        command.append('unicode_marker')
    if sink == 'full':
        if not Path('/dev/full').exists():
            pytest.skip('/dev/full is unavailable on this platform')
        with open('/dev/full', 'wb') as stream:
            result = subprocess.run(command, stdout=stream, stderr=subprocess.PIPE, timeout=20)
    else:
        reader, writer = os.pipe()
        os.close(reader)
        try:
            result = subprocess.run(command, stdout=writer, stderr=subprocess.PIPE, timeout=20)
        finally:
            os.close(writer)
    assert result.returncode == 2, result.stderr
    error = json.loads(result.stderr)
    assert error['status'] == 'ERROR'
    assert 'stdout' in error['recovery']
    assert b'Exception ignored' not in result.stderr
    assert b'Traceback' not in result.stderr
    prepared = json.loads(receipt.read_bytes())
    assert prepared['delivery'] == 'prepared_for_stdout'
    if operation == 'query':
        assert prepared['agent_received'] is None


@pytest.mark.parametrize('stage', ['write', 'flush'])
def test_cancelled_buffered_delivery_is_not_flushed_at_shutdown(tmp_path, stage):
    # Use the real buffered stream, but interrupt after bytes were accepted by
    # its buffer. No signal timing race or product operation is mocked here.
    script = '''
import sys
from types import SimpleNamespace
from codeintel.lab import cli
real = sys.stdout.buffer
class InterruptOutput:
    def write(self, data):
        size = real.write(data)
        if sys.argv[1] == 'write':
            raise KeyboardInterrupt
        return size
    def flush(self):
        raise KeyboardInterrupt
cli.sys.stdout = SimpleNamespace(buffer=InterruptOutput(), fileno=sys.stdout.fileno,
                                flush=real.flush)
cli.main(['index', '--repo', sys.argv[2], '--state-dir', sys.argv[3], '--record', sys.argv[4]])
'''
    repo = tmp_path / 'repo'
    repo.mkdir()
    receipt = tmp_path / 'receipt.json'
    result = subprocess.run([sys.executable, '-c', script, stage, str(repo),
                             str(tmp_path / 'state'), str(receipt)],
                            capture_output=True, timeout=20)
    assert result.returncode == 130, result.stderr
    assert result.stdout == b''
    assert json.loads(result.stderr)['code'] == 'CANCELLED'
    assert json.loads(receipt.read_bytes())['delivery'] == 'prepared_for_stdout'
