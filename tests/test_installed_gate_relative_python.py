"""Keep installed-gate executables absolute without following venv symlinks."""
import argparse
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/verify_installed_package.py'


def gate_setup(tmp_path, monkeypatch, path_form, *, symlink_python=True):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'SNAPSHOT_MANIFEST.json').write_text(json.dumps({
        'files': {}, 'source_commit': 'a' * 40,
    }))
    interpreter = tmp_path / 'clean venv' / 'bin' / 'python'
    interpreter.parent.mkdir(parents=True)
    if symlink_python:
        interpreter.symlink_to(sys.executable)
    else:
        interpreter.write_text('interpreter path fixture\n')
    # A real, harmless executable stand-in exercises launch from the output cwd.
    # A copied-venv binary relocated behind this synthetic symlink cannot find
    # its stdlib on all Python builds. Use the base binary only for the stand-in;
    # the interpreter path being tested above must retain its venv identity.
    interpreter.with_name('codeintel').symlink_to(Path(sys._base_executable).resolve())
    caller = tmp_path / 'caller' if path_form == 'parent-relative' else tmp_path
    caller.mkdir(exist_ok=True)
    monkeypatch.chdir(caller)
    python_arg = interpreter if path_form == 'absolute' else Path(os.path.relpath(interpreter, caller))
    output = tmp_path / 'output'
    monkeypatch.setattr(sys, 'argv', [
        str(SCRIPT), '--source', str(source), '--python', str(python_arg),
        '--output', str(output),
    ])

    spec = importlib.util.spec_from_file_location('installed_gate', SCRIPT)
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    _, _, python, cli, runner = gate.setup(argparse.Namespace(source=source, python=python_arg, output=output, suite_timeout=360))
    namespace = {'python': python, 'cli': cli, 'replacements': runner.replacements, 'run': runner.run}
    return namespace, python_arg.absolute(), output



@pytest.mark.parametrize('path_form', ['relative', 'parent-relative', 'absolute'])
@pytest.mark.parametrize('symlink_python', [False, True])
def test_python_cli_and_sanitization_root_share_absolute_venv_path(
    tmp_path, monkeypatch, path_form, symlink_python,
):
    gate, interpreter, _ = gate_setup(
        tmp_path, monkeypatch, path_form, symlink_python=symlink_python,
    )
    assert gate['python'] == str(interpreter)
    assert gate['cli'] == str(interpreter.parent / 'codeintel')
    assert Path(gate['python']).is_absolute()
    assert Path(gate['cli']).is_absolute()
    assert (str(interpreter.parent.parent), 'CLEAN_VENV') in gate['replacements']
    if symlink_python:
        assert interpreter.is_symlink()
        assert gate['python'] != str(interpreter.resolve())
        assert gate['cli'] != str(interpreter.resolve().parent / 'codeintel')


@pytest.mark.parametrize('path_form', ['relative', 'parent-relative', 'absolute'])
@pytest.mark.parametrize('exit_code', [0, 7])
def test_sibling_cli_launch_and_receipts_use_the_same_absolute_paths(
    tmp_path, monkeypatch, path_form, exit_code,
):
    gate, interpreter, output = gate_setup(tmp_path, monkeypatch, path_form)
    code = (
        'import os,sys; '
        'print(sys.argv[1]); '
        'print(sys.argv[2],file=sys.stderr); '
        'assert os.getcwd() == sys.argv[3]; '
        'sys.exit(int(sys.argv[4]))'
    )
    command = [
        gate['cli'], '-I', '-c', code, gate['python'], gate['cli'],
        str(output), str(exit_code),
    ]
    if exit_code:
        with pytest.raises(RuntimeError, match='version failed: 7'):
            gate['run']('version', command)
    else:
        assert gate['run']('version', command).returncode == 0
        assert not (output / 'receipt.json').exists()

    private = json.loads((output / 'version.command.private.json').read_text())
    assert private['argv'] == command
    assert private['argv'][0] == str(interpreter.parent / 'codeintel')
    execution = json.loads((output / 'execution.json').read_text())
    row, = execution['runs']
    assert row['argv'] == [
        'CLEAN_VENV/bin/codeintel', '-I', '-c', code,
        'CLEAN_VENV/bin/python', 'CLEAN_VENV/bin/codeintel',
        'VALIDATION_OUTPUT', str(exit_code),
    ]
    assert row['argv_sha256'] == hashlib.sha256(
        json.dumps(command, ensure_ascii=False).encode(),
    ).hexdigest()
    assert row['exit_code'] == exit_code
    assert row['timed_out'] is False
    assert row['failure_kind'] == ('exit' if exit_code else None)
    assert (output / 'version.stdout.txt').read_text() == 'CLEAN_VENV/bin/python\n'
    assert (output / 'version.stderr.txt').read_text() == 'CLEAN_VENV/bin/codeintel\n'
    public_paths = [output / 'execution.json', output / 'version.stdout.txt', output / 'version.stderr.txt']
    if exit_code:
        receipt_path = output / 'receipt.json'
        receipt = json.loads(receipt_path.read_text())
        assert receipt['status'] == 'FAIL'
        assert receipt['failed_stage'] == 'version'
        assert receipt['runs'] == execution['runs']
        public_paths.append(receipt_path)
    for path in public_paths:
        assert str(tmp_path) not in path.read_text()
        assert str(interpreter.parent.parent) not in path.read_text()
