"""Exercise the real install-gate runner without building or testing a wheel."""
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import stat
import time

import pytest
import codeintel
from tests.validation_receipt_fixture import write_source_receipt

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/verify_installed_package.py'


def runner(out):
    spec = importlib.util.spec_from_file_location('installed_gate', SCRIPT)
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    return gate.contract.StageRunner(out, cwd=out,
        replacements=[(str(out), 'VALIDATION_OUTPUT')],
        receipt_base={'source_commit': 'a' * 40}).run



@pytest.mark.parametrize('kind', ['timeout', 'exit', 'launch'])
def test_failed_stage_preserves_partial_logs_and_explicit_receipt(tmp_path, kind):
    run = runner(tmp_path)
    if kind == 'launch':
        command, deadline = [str(tmp_path / 'missing-python')], 2
    else:
        code = "import sys,time;print('observed stdout',flush=True);print('observed stderr',file=sys.stderr,flush=True);"
        code += 'time.sleep(10)' if kind == 'timeout' else 'sys.exit(7)'
        command, deadline = [sys.executable, '-I', '-c', code], (1 if kind == 'timeout' else 2)
    with pytest.raises(RuntimeError):
        run('probe', command, timeout=deadline)
    receipt = json.loads((tmp_path / 'receipt.json').read_text())
    assert receipt['status'] == 'FAIL'
    assert receipt['failed_stage'] == 'probe'
    assert receipt['source_commit'] == 'a' * 40
    row, = receipt['runs']
    assert row['timed_out'] is (kind == 'timeout')
    assert row['failure_kind'] == kind
    assert row['exit_code'] == {'timeout': -15, 'exit': 7, 'launch': None}[kind]
    assert row['deadline_seconds'] == deadline
    exact = json.loads((tmp_path / 'probe.command.private.json').read_text())
    assert exact['argv'] == command
    assert row['argv_sha256'] == hashlib.sha256(json.dumps(command, ensure_ascii=False).encode()).hexdigest()
    assert str(tmp_path) not in (tmp_path / 'receipt.json').read_text()
    if kind != 'launch':
        assert (tmp_path / 'probe.stdout.txt').read_text() == 'observed stdout\n'
        assert (tmp_path / 'probe.stderr.txt').read_text() == 'observed stderr\n'
    assert json.loads((tmp_path / 'execution.json').read_text())['runs'] == receipt['runs']


def test_successful_stage_keeps_exact_command_without_claiming_overall_pass(tmp_path):
    command = [sys.executable, '-I', '-c', 'print("fixture ok")']
    result = runner(tmp_path)('probe', command)
    assert result.stdout == b'fixture ok\n'
    assert not (tmp_path / 'receipt.json').exists()
    row, = json.loads((tmp_path / 'execution.json').read_text())['runs']
    assert row['exit_code'] == 0 and row['timed_out'] is False
    assert json.loads((tmp_path / 'probe.command.private.json').read_text())['argv'] == command


def test_entry_script_failure_dispatch_stops_before_build(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'SNAPSHOT_MANIFEST.json').write_text(json.dumps({'files': {}, 'source_commit': 'a' * 40}))
    output = tmp_path / 'output'
    acceptance = write_source_receipt(tmp_path / 'source-receipt.json')
    wrapper = r"""import importlib.util,sys
from pathlib import Path
script,source,interpreter,output,acceptance,runtime=sys.argv[1:]
sys.path.insert(0,runtime)
from codeintel.bounded_process import BoundedProcessResult
spec=importlib.util.spec_from_file_location('installed_gate',script)
gate=importlib.util.module_from_spec(spec);spec.loader.exec_module(gate)
def timed_out(cmd,**kwargs):
 return BoundedProcessResult(tuple(cmd),-15,b'partial probe output\n',b'partial probe error\n',21,20,True)
gate.contract.run_bounded_process=timed_out
gate.main(['--source',source,'--python',interpreter,'--output',output,'--source-receipt',acceptance])
"""
    result = subprocess.run([sys.executable, '-I', '-c', wrapper, str(SCRIPT), str(source), sys.executable, str(output), str(acceptance),
                             str(Path(codeintel.__file__).resolve().parent.parent)], capture_output=True, timeout=5)
    assert result.returncode != 0
    receipt = json.loads((output / 'receipt.json').read_text())
    assert receipt['status'] == 'FAIL' and receipt['failed_stage'] == 'fresh-env'
    assert (output / 'fresh-env.stdout.txt').read_text() == 'partial probe output\n'
    assert not (output / 'build-source').exists()
    assert not (output / 'wheels').exists()
    assert not (output / 'install-wheel.command.private.json').exists()


@pytest.mark.skipif(os.name != 'posix', reason='POSIX file modes')
def test_exact_command_file_is_private_and_exclusive(tmp_path, monkeypatch):
    command = [sys.executable, '-I', '-c', 'print("fixture")']
    run = runner(tmp_path)
    run('probe', command)
    path = tmp_path / 'probe.command.private.json'
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    original = path.read_bytes()
    def forbidden(*args, **kwargs):
        pytest.fail('An existing command receipt must reject before another process starts')
    monkeypatch.setitem(run.__func__.__globals__, 'run_bounded_process', forbidden)
    with pytest.raises(FileExistsError):
        run('probe', command)
    assert path.read_bytes() == original
