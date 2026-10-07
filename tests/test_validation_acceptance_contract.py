"""Acceptance is defined by observed cases and source identity, not exit 0 alone."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location('gate_' + name, ROOT / 'scripts' / (name + '.py'))
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


contract = module('validation_contract')


def junit(name='test_one', child='', *, tests=1, failures=0, errors=0, skipped=0):
    return (f'<testsuites><testsuite tests="{tests}" failures="{failures}" errors="{errors}" skipped="{skipped}">'
            f'<testcase classname="tests.test_fixture" name="{name}">{child}</testcase>'
            '</testsuite></testsuites>')


@pytest.mark.parametrize('kind', ['valid', 'skipped', 'dirty', 'missing', 'malformed', 'launch', 'timeout', 'cancel'])
def test_source_gate_is_fail_closed_and_preserves_attempt_receipt(tmp_path, monkeypatch, kind):
    gate = module('validate_portfolio')
    repo = tmp_path / 'repository'
    (repo / 'tests').mkdir(parents=True)
    path = repo / 'tests/test_fixture.py'
    path.write_text('def test_one():\n    pass\n')
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Fixture', '-c', 'user.email=fixture@localhost',
                    'commit', '-qm', 'authored fixture'], check=True)
    output = tmp_path / 'output'
    monkeypatch.setattr(gate, 'ROOT', repo)
    monkeypatch.setattr(sys, 'argv', ['validate_portfolio.py', '--output', str(output)])
    monkeypatch.setenv('PYTEST_ADDOPTS', '-k never_run')
    monkeypatch.setenv('PYTEST_PLUGINS', 'unreviewed_plugin')
    from codeintel.bounded_process import BoundedProcessResult
    real_run = gate.contract.run_bounded_process

    def run(argv, **kwargs):
        if '-c' not in argv:
            return real_run(argv, **kwargs)
        assert 'PYTEST_ADDOPTS' not in kwargs['env'] and 'PYTEST_PLUGINS' not in kwargs['env']
        assert kwargs['env']['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] == '1'
        if kind == 'launch':
            raise FileNotFoundError('authored launch failure')
        if kind == 'cancel':
            raise KeyboardInterrupt('authored cancellation')
        if kind == 'timeout':
            return BoundedProcessResult(tuple(argv), -15, b'partial output\n', b'', 15, 0, True)
        collection_path = Path(argv[-5])
        collection = gate.contract.collection_report(['tests/test_fixture.py::test_one'])
        collection['pytest_exit_code'] = 0
        collection_path.write_text(json.dumps(collection))
        if argv[-2]:
            if kind != 'missing':
                raw = '<unfinished' if kind == 'malformed' else junit(
                    child='<skipped/>' if kind == 'skipped' else '', skipped=int(kind == 'skipped'))
                Path(argv[-2]).write_text(raw)
            if kind == 'dirty':
                path.write_text('changed after execution\n')
        return BoundedProcessResult(tuple(argv), 0, b'authored runner output\n', b'', 23, 0, False)

    monkeypatch.setattr(gate.contract, 'run_bounded_process', run)
    with pytest.raises(SystemExit) as exit_info:
        gate.main()
    receipt = json.loads((output / 'receipt.json').read_text())
    assert exit_info.value.code == (0 if kind == 'valid' else 1)
    assert receipt['status'] == ('PASS' if kind == 'valid' else 'FAIL')
    assert receipt['clean_after'] is (kind != 'dirty')
    assert bool(receipt['failure_reasons']) is (kind != 'valid')
    assert (output / 'collect-tests.stdout.txt').exists() and (output / 'collect-tests.stderr.txt').exists()
    if kind == 'valid':
        assert receipt['case_ids'] == [['tests.test_fixture', 'test_one']]
        assert set(receipt['source_file_sha256']) == {'tests/test_fixture.py'}
    if kind == 'cancel':
        assert receipt['cancelled'] is True
    if kind in ('launch', 'timeout', 'cancel'):
        assert receipt['exit_code'] is None


@pytest.mark.parametrize('raw', [
    '<not-junit/>', '<testsuites/>',
    junit(tests=2), junit(errors=1),
    junit().replace('classname="tests.test_fixture"', ''),
    junit().replace('</testsuite>', '<testcase classname="tests.test_fixture" name="test_one"/></testsuite>').replace('tests="1"', 'tests="2"'),
])
def test_junit_schema_counts_and_duplicate_ids_are_rejected(tmp_path, raw):
    path = tmp_path / 'result.xml'
    path.write_text(raw)
    with pytest.raises(ValueError):
        contract.read_junit(path)


def source_receipt(tmp_path):
    path = tmp_path / 'source.xml'
    path.write_text(junit())
    report = contract.read_junit(path)
    report['collection'] = contract.collection_report(['tests/test_fixture.py::test_one'])
    report['execution_collection'] = deepcopy(report['collection'])
    return {'schema': 'portfolio-consolidation-v3', 'status': 'PASS',
            'tested_source_commit': 'a' * 40, 'clean_before': True, 'clean_after': True,
            'exit_code': 0, 'source_file_sha256': {'source.py': 'b' * 64}, **report}


@pytest.mark.parametrize('defect', ['status', 'commit', 'dirty', 'skip', 'digest', 'source_hash', 'empty', 'duplicate'])
def test_installed_gate_requires_bound_passing_source_receipt(tmp_path, defect):
    receipt = source_receipt(tmp_path)
    if defect == 'status': receipt['status'] = 'FAIL'
    elif defect == 'commit': receipt['tested_source_commit'] = 'c' * 40
    elif defect == 'dirty': receipt['clean_after'] = False
    elif defect == 'skip': receipt['junit']['skipped'] = 1
    elif defect == 'digest': receipt['case_ids_sha256'] = '0' * 64
    elif defect == 'source_hash': receipt['source_file_sha256']['source.py'] = '0' * 64
    elif defect == 'empty': receipt['case_ids'] = []; receipt['junit']['tests'] = 0
    else: receipt['case_ids'] *= 2; receipt['junit']['tests'] = 2
    with pytest.raises(ValueError):
        contract.validate_source_receipt(receipt, 'a' * 40, {'source.py': 'b' * 64})


@pytest.mark.parametrize('different', [False, True])
def test_source_and_installed_case_identity_must_match_not_just_count(tmp_path, different):
    source = source_receipt(tmp_path)
    contract.validate_source_receipt(source, 'a' * 40, {'source.py': 'b' * 64})
    installed = deepcopy(source)
    if different:
        installed['case_ids'][0][1] = 'test_replacement'
        installed['case_ids_sha256'] = contract.case_digest(installed['case_ids'])
        with pytest.raises(ValueError, match='identities differ'):
            contract.require_matching_cases(source, installed)
    else:
        contract.require_matching_cases(source, installed)


def test_coherent_common_junit_omission_is_rejected_by_independent_collection(tmp_path):
    source = source_receipt(tmp_path)
    collected_ids = [['tests.test_fixture', 'test_one'], ['tests.test_fixture', 'test_two']]
    source['collection'] = contract.collection_report(['tests/test_fixture.py::test_one', 'tests/test_fixture.py::test_two'])
    # Both JUnit-derived reports coherently omit the same independently collected case.
    installed = deepcopy(source)
    with pytest.raises(ValueError, match='collection'):
        contract.require_matching_cases(source, installed)
