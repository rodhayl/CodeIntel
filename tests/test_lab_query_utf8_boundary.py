"""Malformed text arguments fail before service creation or source disclosure."""
import json
import os
import subprocess
import sys

import pytest

from codeintel.lab import retrieval


@pytest.mark.parametrize('query', ['\ud800', '\udcff', 'private_marker\udfff'])
def test_non_utf8_query_rejected_before_opening_service(tmp_path, monkeypatch, query):
    def unexpected(*args, **kwargs):
        pytest.fail('invalid query must not open storage or inspect source')
    monkeypatch.setattr(retrieval, 'open_service', unexpected)
    with pytest.raises(retrieval.LabError) as caught:
        retrieval.query_repository(tmp_path, tmp_path / 'state', query)
    assert caught.value.code == 'INVALID_QUERY'
    assert 'private_marker' not in caught.value.recovery


@pytest.mark.skipif(os.name != 'posix', reason='POSIX raw argument bytes')
@pytest.mark.parametrize('literal', [False, True])
def test_non_utf8_argv_has_structured_cli_recovery(tmp_path, literal):
    repo = tmp_path / 'repo'
    repo.mkdir()
    state, receipt = tmp_path / 'state', tmp_path / 'receipt.json'
    argv = [os.fsencode(sys.executable), b'-m', b'codeintel.lab.cli', b'query',
            b'--repo', os.fsencode(repo), b'--state-dir', os.fsencode(state),
            b'--record', os.fsencode(receipt), b'private_marker\xff']
    if literal:
        argv.append(b'--literal')
    result = subprocess.run(argv, capture_output=True, timeout=5, check=False)
    assert result.returncode == 2
    assert not result.stdout
    assert json.loads(result.stderr)['code'] == 'INVALID_QUERY'
    assert b'Traceback' not in result.stderr and b'private_marker' not in result.stderr
    assert os.fsencode(tmp_path) not in result.stderr
    assert not state.exists() and not receipt.exists()
