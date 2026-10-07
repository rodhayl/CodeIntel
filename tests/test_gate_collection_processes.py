"""Real collection/JUnit and process ownership at both gate boundaries."""
import importlib.util
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import time

import pytest
import codeintel

ROOT = Path(__file__).resolve().parents[1]


def load_contract():
    spec = importlib.util.spec_from_file_location('gate_contract', ROOT / 'scripts/validation_contract.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_real_independent_collection_and_full_suite_preserve_parameter_ids(tmp_path):
    contract = load_contract()
    source = tmp_path / 'source'
    (source / 'tests').mkdir(parents=True)
    (source / 'tests/test_authored.py').write_text(
        "import pytest\n@pytest.mark.parametrize('value', range(29), ids=lambda v: f'a//b::c-{v}')\n"
        "def test_parameter(value):\n    assert value >= 0\n"
    )
    out = tmp_path / 'output'
    out.mkdir()
    # This synthetic source has no runtime package; use the actual venv origin.
    runtime = subprocess.check_output([sys.executable, '-I', '-c',
        'import codeintel;from pathlib import Path;print(Path(codeintel.__file__).parent)'], text=True).strip()
    runner = contract.StageRunner(out, cwd=out)
    report = contract.run_pytest_suite(runner, python=sys.executable, source=source,
        runtime_root=runtime, mode='installed', junit_name='installed.junit.xml')
    assert report['junit'] == {'tests': 29, 'failures': 0, 'errors': 0, 'skipped': 0}
    assert report['execution_mode'] == 'monolithic'
    assert [row['name'] for row in runner.runs] == ['collect-tests', 'full-suite']
    assert report['execution_collection']['node_ids'] == report['collection']['node_ids']
    assert report['suite_deadline_seconds'] == 360
    assert report['collection']['case_ids'] == report['case_ids']
    assert len(report['collection']['node_ids']) == 29
    assert all('a//b::c-' in node for node in report['collection']['node_ids'])
    assert all('-I' in row['argv'] and '-B' in row['argv'] for row in runner.runs)
    assert report['collection']['runtime_origins']['codeintel.bounded_process']['path'] == 'bounded_process.py'


@pytest.mark.parametrize('defect', ['junit', 'collection', 'both', 'duplicate', 'digest'])
def test_complete_cases_reject_omissions_and_collection_corruption(tmp_path, defect):
    contract = load_contract()
    collection = contract.collection_report(['tests/test_a.py::test_one', 'tests/test_a.py::test_two'])
    ids = list(collection['case_ids'])
    report = {'junit': {'tests': 2, 'failures': 0, 'errors': 0, 'skipped': 0},
              'case_ids': ids, 'case_ids_sha256': contract.case_digest(ids), 'collection': collection,
              'execution_collection': contract.collection_report(collection['node_ids'])}
    if defect in ('junit', 'both'):
        report['case_ids'] = ids[:1]
        report['junit']['tests'] = 1
        report['case_ids_sha256'] = contract.case_digest(ids[:1])
    if defect == 'collection':
        report['collection'] = contract.collection_report(['tests/test_a.py::test_one'])
    elif defect == 'duplicate':
        collection['node_ids'].append(collection['node_ids'][0])
    elif defect == 'digest':
        collection['node_ids_sha256'] = '0' * 64
    # "both" represents the same coherent omission in source and installed JUnit,
    # not modification of the independent collection artifact.
    with pytest.raises(ValueError):
        if defect == 'both':
            contract.require_matching_cases(report, report)
        else:
            contract.require_complete_cases(report)


def test_runtime_origin_mismatch_is_rejected_without_pass(tmp_path):
    contract = load_contract()
    plugin = contract.CollectionPlugin(tmp_path / 'collection.json', tmp_path / 'wrong-runtime')
    plugin.nodes = ['tests/test_a.py::test_one']
    with pytest.raises(ValueError, match='Runtime import escaped'):
        plugin.pytest_sessionfinish(None, 0)
    assert not plugin.output.exists()


@pytest.mark.parametrize('stream', ['stdout', 'stderr'])
def test_truncated_stage_output_is_an_explicit_failure(tmp_path, stream, monkeypatch):
    contract = load_contract()
    monkeypatch.setattr(contract, 'OUTPUT_LIMIT', 64)
    runner = contract.StageRunner(tmp_path, cwd=tmp_path)
    with pytest.raises(contract.StageFailure, match='output_limit'):
        runner.run('overflow', [sys.executable, '-I', '-c', f'import sys;sys.{stream}.write("x"*1000)'])
    receipt = json.loads((tmp_path / 'receipt.json').read_text())
    row, = receipt['runs']
    assert row['observed_' + stream + '_bytes'] == 1000
    assert row['captured_' + stream + '_bytes'] == 64
    assert row['failure_kind'] == 'output_limit' and receipt['status'] == 'FAIL'
    assert row['output_capture_complete'] is False


@pytest.mark.skipif(os.name != 'posix' or not hasattr(os, 'pidfd_open'), reason='Linux POSIX process ownership')
@pytest.mark.parametrize('gate', ['validate_portfolio', 'verify_installed_package'])
@pytest.mark.parametrize('outcome', ['timeout', 'cancel'])
def test_gate_stage_kills_real_descendant_and_preserves_honest_receipt(tmp_path, gate, outcome):
    pid_path = tmp_path / 'descendant.pid'
    ready = tmp_path / 'ready'
    # The descendant closes inherited pipes and ignores TERM, so leader-only
    # termination or merely draining output cannot pass this test.
    descendant_ready = tmp_path / 'descendant.ready'
    descendant = ("import os,pathlib,signal,time;os.close(1);os.close(2);signal.signal(signal.SIGTERM,signal.SIG_IGN);"
                  f"pathlib.Path({str(descendant_ready)!r}).write_text('ready');time.sleep(60)")
    child = ("import pathlib,subprocess,sys,time;"
             f"p=subprocess.Popen([sys.executable,'-I','-c',{descendant!r}]);"
             f"exec(\"while not pathlib.Path({str(descendant_ready)!r}).exists(): time.sleep(0.005)\");"
             f"pathlib.Path({str(pid_path)!r}).write_text(str(p.pid));"
             "print('started child',flush=True);time.sleep(60)")
    harness = """import importlib.util,sys
from pathlib import Path
root,gate,out,ready,child,outcome,runtime=sys.argv[1:]
sys.path.insert(0,runtime)
spec=importlib.util.spec_from_file_location('gate_under_test',Path(root)/'scripts'/(gate+'.py'))
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
module.contract.install_cancellation_handler()
runner=module.contract.StageRunner(Path(out),cwd=Path(out),receipt_base={'source_commit':'a'*40})
Path(ready).write_text('ready')
runner.run('synthetic',[sys.executable,'-I','-c',child],timeout=0.5 if outcome=='timeout' else 30)
"""
    process = subprocess.Popen([sys.executable, '-I', '-c', harness, str(ROOT), gate,
                                str(tmp_path), str(ready), child, outcome,
                                str(Path(codeintel.__file__).resolve().parent.parent)],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    sibling = subprocess.Popen([sys.executable, '-I', '-c', 'import time;time.sleep(60)'])
    descendant_pid = pidfd = None
    try:
        deadline = time.monotonic() + 5
        while not pid_path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert pid_path.exists()
        descendant_pid = int(pid_path.read_text())
        pidfd = os.pidfd_open(descendant_pid)
        if outcome == 'cancel':
            process.send_signal(signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=8)
        assert process.returncode != 0, (stdout, stderr)
        assert select.select([pidfd], [], [], 2)[0], 'Owned descendant survived the gate'
        assert sibling.poll() is None, 'Gate cleanup signalled an unrelated process'
        receipt = json.loads((tmp_path / 'receipt.json').read_text())
        row, = receipt['runs']
        assert receipt['status'] == 'FAIL'
        assert receipt['failed_stage'] == 'synthetic'
        assert row['failure_kind'] == ('timeout' if outcome == 'timeout' else 'cancelled')
        assert row['process_owner'] == 'codeintel.bounded_process'
        if outcome == 'cancel':
            assert row['exit_code'] is None and row['output_capture_complete'] is False
            assert row['observed_stdout_bytes'] is None
        else:
            assert row['timed_out'] is True
            assert (tmp_path / 'synthetic.stdout.txt').read_text() == 'started child\n'
    finally:
        sibling.terminate()
        sibling.wait(timeout=5)
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=8)
        if pidfd is not None and not select.select([pidfd], [], [], 0)[0]:
            signal.pidfd_send_signal(pidfd, signal.SIGKILL)
        if pidfd is not None:
            os.close(pidfd)



@pytest.mark.parametrize('value', ['0', '-1', 'nan', 'inf', '901', 'not-a-number'])
def test_suite_timeout_rejects_unbounded_or_invalid_values(value):
    with pytest.raises(ValueError):
        load_contract().suite_timeout(value)


@pytest.mark.parametrize('value', ['0.01', '360', '900'])
def test_suite_timeout_accepts_explicit_bounded_values(value):
    assert load_contract().suite_timeout(value) == float(value)
