"""Shared gate contracts and stage receipts around the runtime process owner."""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import sys
import time
import xml.etree.ElementTree as ET

from codeintel.bounded_process import run_bounded_process


def require(condition, message):
    if not condition:
        raise ValueError(message)


def case_digest(case_ids):
    return hashlib.sha256(json.dumps(case_ids, ensure_ascii=True, separators=(',', ':')).encode()).hexdigest()


def read_junit(path):
    root = ET.parse(path).getroot()
    require(root.tag in ('testsuites', 'testsuite'), 'Invalid JUnit root')
    suites = [suite for suite in root.iter('testsuite') if not list(suite.iterfind('testsuite'))]
    require(bool(suites), 'JUnit contains no test suites')
    cases = list(root.iter('testcase'))
    ids = []
    for case in cases:
        classname, name = case.get('classname'), case.get('name')
        require(bool(classname) and bool(name), 'JUnit case identity is incomplete')
        ids.append([classname, name])
    ids.sort()
    require(len({tuple(value) for value in ids}) == len(ids), 'Duplicate JUnit case identity')
    counts = {'tests': len(cases), 'failures': sum(case.find('failure') is not None for case in cases),
              'errors': sum(case.find('error') is not None for case in cases),
              'skipped': sum(case.find('skipped') is not None for case in cases)}
    for key, observed in counts.items():
        declared = []
        for suite in suites:
            value = suite.get(key)
            require(isinstance(value, str) and re.fullmatch(r'0|[1-9][0-9]*', value) is not None,
                    'Invalid JUnit count: ' + key)
            declared.append(int(value))
        require(sum(declared) == observed, 'JUnit count differs from case records: ' + key)
    return {'junit': counts, 'case_ids': ids, 'case_ids_sha256': case_digest(ids)}


def require_passing_cases(report):
    counts, ids = report.get('junit'), report.get('case_ids')
    require(isinstance(counts, dict) and isinstance(ids, list), 'Missing case acceptance data')
    require(all(isinstance(pair, list) and len(pair) == 2
                and all(isinstance(value, str) and value for value in pair) for pair in ids),
            'Invalid test case identities')
    require(ids == sorted(ids) and len({tuple(value) for value in ids}) == len(ids),
            'Test case identities must be unique and sorted')
    require(ids and type(counts.get('tests')) is int and counts['tests'] == len(ids),
            'Acceptance requires a nonempty internally consistent case set')
    require(all(type(counts.get(key)) is int and counts[key] == 0
                for key in ('failures', 'errors', 'skipped')), 'Acceptance requires zero failures, errors and skips')
    require(report.get('case_ids_sha256') == case_digest(ids), 'Test case identity digest differs')


def validate_source_receipt(receipt, source_commit, source_files):
    require(isinstance(receipt, dict), 'Source receipt must be an object')
    require(receipt.get('schema') == 'portfolio-consolidation-v3' and receipt.get('status') == 'PASS',
            'A passing current source-gate receipt is required')
    require(receipt.get('tested_source_commit') == source_commit, 'Source receipt belongs to another commit')
    require(receipt.get('clean_before') is True and receipt.get('clean_after') is True,
            'Source receipt requires a clean checkout before and after')
    require(type(receipt.get('exit_code')) is int and receipt['exit_code'] == 0,
            'Source test process did not exit successfully')
    require_complete_cases(receipt)
    hashes = receipt.get('source_file_sha256')
    require(isinstance(hashes, dict), 'Source receipt lacks file identities')
    require(all(hashes.get(name) == digest for name, digest in source_files.items()),
            'Source receipt does not cover the curated source bytes')
    return receipt['case_ids']


def require_matching_cases(source_receipt, installed_report):
    require_complete_cases(source_receipt)
    require_complete_cases(installed_report)
    require(source_receipt['collection']['node_ids'] == installed_report['collection']['node_ids'],
            'Installed collection differs from the accepted source collection')
    require(source_receipt['case_ids'] == installed_report['case_ids'],
            'Installed test identities differ from the accepted source suite')


# The original 90-second gate is historical evidence, not silently reinterpreted.
# The declared configurable suite budget is an operational change, not a speedup.
DEFAULT_SUITE_SECONDS = 360
MAX_SUITE_SECONDS = 900
COLLECTION_SECONDS = 30
OUTPUT_LIMIT = 2 * 1024 * 1024


def suite_timeout(value):
    seconds = float(value)
    require(math.isfinite(seconds) and 0 < seconds <= MAX_SUITE_SECONDS,
            f'suite timeout must be finite, positive and at most {MAX_SUITE_SECONDS} seconds')
    return seconds


def normalized_case_id(node_id):
    # Split parameters only once: '/' and '::' inside a parameter are literal.
    address, bracket, parameters = node_id.partition('[')
    parts = address.split('::')
    require(len(parts) >= 2 and parts[0].startswith('tests/') and parts[0].endswith('.py'),
            'Invalid collection node identity')
    require('..' not in parts[0].split('/'), 'Invalid collection node path')
    parts[0] = parts[0][:-3].replace('/', '.')
    parts[-1] += bracket + parameters
    return ['.'.join(parts[:-1]), parts[-1]]


def collection_report(node_ids, origins=None):
    ids = sorted(node_ids)
    pairs = sorted(normalized_case_id(value) for value in ids)
    report = {'schema': 'codeintel-pytest-collection-v1', 'node_ids': ids,
              'node_ids_sha256': case_digest(ids), 'case_ids': pairs,
              'case_ids_sha256': case_digest(pairs), 'runtime_origins': origins or {}}
    require_collection(report)
    return report


def require_collection(report):
    require(isinstance(report, dict) and report.get('schema') == 'codeintel-pytest-collection-v1',
            'Missing independent collection report')
    nodes = report.get('node_ids')
    require(isinstance(nodes, list) and nodes and all(isinstance(node, str) and node for node in nodes),
            'Missing collection node identities')
    require(nodes == sorted(set(nodes)), 'Collection identities must be unique and sorted')
    require(report.get('node_ids_sha256') == case_digest(nodes), 'Collection node digest differs')
    pairs = sorted(normalized_case_id(node) for node in nodes)
    require(len({tuple(pair) for pair in pairs}) == len(pairs), 'Ambiguous collection JUnit identities')
    require(report.get('case_ids') == pairs and report.get('case_ids_sha256') == case_digest(pairs),
            'Collection case identities differ')


def require_complete_cases(report):
    require_passing_cases(report)
    collection = report.get('collection')
    require_collection(collection)
    require(report['case_ids'] == collection['case_ids'], 'JUnit identities differ from independent collection')
    executed = report.get('execution_collection')
    require_collection(executed)
    require(executed['node_ids'] == collection['node_ids'],
            'Executed collection differs from independent collection')


class StageFailure(RuntimeError):
    pass


class StageRunner:
    """Receipt/log adapter around the single runtime process owner."""

    def __init__(self, out, *, cwd, env=None, replacements=(), receipt_base=None, total_seconds=1200):
        self.out, self.cwd = Path(out), Path(cwd)
        self.env = dict(os.environ if env is None else env)
        self.env.pop('PYTEST_ADDOPTS', None)
        self.env.pop('PYTEST_PLUGINS', None)
        self.env['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
        self.replacements = replacements
        self.receipt_base = receipt_base or {}
        self.runs = []
        self.deadline = time.monotonic() + total_seconds

    def clean(self, text):
        for old, new in self.replacements:
            text = text.replace(old, new)
        return re.sub(r'/tmp/pytest-of-[^/\s]+', 'PYTEST_TEMP', text)

    def fail(self, stage, exc):
        receipt = {**self.receipt_base, 'status': 'FAIL', 'failed_stage': stage,
                   'failure_kind': 'cancelled' if isinstance(exc, KeyboardInterrupt) else 'validation',
                   'failure_message': self.clean(str(exc)), 'runs': self.runs,
                   'model_calls': 0, 'software_production_ready': False}
        (self.out / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')

    def run(self, name, argv, timeout=25):
        exact = [str(value) for value in argv]
        fd = os.open(self.out / (name + '.command.private.json'),
                     os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(json.dumps({'argv': exact}, ensure_ascii=False, indent=2) + '\n')
        start = time.perf_counter()
        effective_timeout = min(timeout, self.deadline - time.monotonic())
        result, error, kind = None, None, None
        try:
            if effective_timeout <= 0:
                raise StageFailure('overall gate deadline expired')
            result = run_bounded_process(exact, cwd=str(self.cwd), env=self.env,
                                         timeout_seconds=effective_timeout,
                                         stdout_limit=OUTPUT_LIMIT, stderr_limit=OUTPUT_LIMIT)
            if result.timed_out:
                kind = 'timeout'
            elif result.observed_stdout_bytes > len(result.stdout) or result.observed_stderr_bytes > len(result.stderr):
                kind = 'output_limit'
            elif result.returncode:
                kind = 'exit'
        except (KeyboardInterrupt, Exception) as exc:
            error = exc
            kind = 'cancelled' if isinstance(exc, KeyboardInterrupt) else 'launch' if isinstance(exc, OSError) else 'execution'
        stdout = result.stdout if result else b''
        stderr = result.stderr if result else str(error).encode()
        (self.out / (name + '.stdout.txt')).write_text(self.clean(stdout.decode(errors='replace')))
        (self.out / (name + '.stderr.txt')).write_text(self.clean(stderr.decode(errors='replace')))
        row = {'name': name, 'argv': [self.clean(value) for value in exact],
               'argv_sha256': hashlib.sha256(json.dumps(exact, ensure_ascii=False).encode()).hexdigest(),
               'exit_code': result.returncode if result else None,
               'timed_out': bool(result and result.timed_out), 'failure_kind': kind,
               'elapsed_seconds': time.perf_counter() - start, 'deadline_seconds': timeout,
               'effective_deadline_seconds': max(0, effective_timeout),
               'captured_stdout_bytes': len(stdout), 'captured_stderr_bytes': len(stderr) if result else 0,
               'observed_stdout_bytes': result.observed_stdout_bytes if result else None,
               'observed_stderr_bytes': result.observed_stderr_bytes if result else None,
               'output_capture_complete': bool(result and result.observed_stdout_bytes == len(result.stdout)
                                               and result.observed_stderr_bytes == len(result.stderr)),
               'process_owner': 'codeintel.bounded_process',
               'cleanup_scope': 'owned POSIX process group; detached sessions and SIGKILL of gate excluded'}
        self.runs.append(row)
        (self.out / 'execution.json').write_text(json.dumps({'schema': 'codeintel-gate-execution-v2', 'runs': self.runs}, indent=2) + '\n')
        if kind:
            failure = error or StageFailure(f'{name} failed: {result.returncode if kind == "exit" else kind}')
            self.fail(name, failure)
            if isinstance(error, KeyboardInterrupt):
                raise error
            raise StageFailure(f'{name} failed: {result.returncode if kind == "exit" else kind}') from error
        return result


class CollectionPlugin:
    """Observe real pytest collection independently from its JUnit writer."""

    def __init__(self, output, runtime_root):
        self.output = Path(output)
        self.runtime_root = Path(runtime_root).resolve()
        self.nodes = []

    def pytest_collection_finish(self, session):
        self.nodes = [item.nodeid for item in session.items]

    def pytest_sessionfinish(self, session, exitstatus):
        origins = {}
        for name, module in list(sys.modules.items()):
            if name == 'codeintel' or name.startswith('codeintel.'):
                origin = getattr(module, '__file__', None)
                if origin:
                    actual = Path(origin).resolve()
                    require(actual.is_relative_to(self.runtime_root), 'Runtime import escaped expected origin: ' + name)
                    origins[name] = {'path': actual.relative_to(self.runtime_root).as_posix(),
                                     'sha256': hashlib.sha256(actual.read_bytes()).hexdigest()}
        report = collection_report(self.nodes, origins)
        report['runtime_root'] = str(self.runtime_root)
        report['origin_scope'] = 'codeintel modules loaded in this pytest process at session finish; not all child processes'
        report['pytest_exit_code'] = int(exitstatus)
        self.output.write_text(json.dumps(report, indent=2) + '\n')


PYTEST_LAUNCHER = """import importlib.util,sys
from pathlib import Path
helper,source,output,runtime,collect_only,junit,mode=sys.argv[1:]
if mode=='source':sys.path.insert(0,source)
spec=importlib.util.spec_from_file_location('codeintel_gate_contract',helper)
contract=importlib.util.module_from_spec(spec);spec.loader.exec_module(contract)
import pytest
args=['-q','--strict-markers','--import-mode=importlib','-o','pythonpath=','-o','addopts=','-o','cache_dir='+str(Path(output).parent/'pytest-cache'),'--rootdir='+source]
if collect_only=='yes':args += ['--collect-only']
args += [source+'/tests']
if junit:args += ['--junitxml='+junit]
raise SystemExit(pytest.main(args,plugins=[contract.CollectionPlugin(output,runtime)]))
"""


def run_pytest_suite(runner, *, python, source, runtime_root, mode, junit_name,
                     timeout_seconds=DEFAULT_SUITE_SECONDS):
    source = Path(source)
    timeout_seconds = suite_timeout(timeout_seconds)
    helper = Path(__file__).resolve()
    def invoke(name, report_path, *, collect_only=False, junit=''):
        return runner.run(name, [str(python), '-I', '-B', '-c', PYTEST_LAUNCHER,
                                str(helper), str(source), str(report_path), str(runtime_root),
                                'yes' if collect_only else 'no', str(junit), mode],
                          timeout=COLLECTION_SECONDS if collect_only else timeout_seconds)
    collection_path = runner.out / 'collection.json'
    invoke('collect-tests', collection_path, collect_only=True)
    collection = json.loads(collection_path.read_text())
    require_collection(collection)
    require(collection.get('pytest_exit_code') == 0, 'Independent collection failed')
    execution_path = runner.out / 'executed-collection.json'
    junit_path = runner.out / junit_name
    invoke('full-suite', execution_path, junit=junit_path)
    executed = json.loads(execution_path.read_text())
    require_collection(executed)
    require(executed.get('pytest_exit_code') == 0, 'Executed collection did not pass')
    require(executed['node_ids'] == collection['node_ids'],
            'Executed collection differs from independent collection')
    raw_digest = hashlib.sha256(junit_path.read_bytes()).hexdigest()
    junit_path.write_text(runner.clean(junit_path.read_text()))
    collection_path.write_text(runner.clean(collection_path.read_text()))
    execution_path.write_text(runner.clean(execution_path.read_text()))
    report = {**read_junit(junit_path), 'collection': json.loads(collection_path.read_text()),
              'execution_collection': json.loads(execution_path.read_text()),
              'execution_mode': 'monolithic', 'suite_deadline_seconds': timeout_seconds,
              'collection_deadline_seconds': COLLECTION_SECONDS,
              'junit_before_path_sanitization_sha256': raw_digest}
    require_complete_cases(report)
    return report


def cancellation_handler(signum, frame):
    raise KeyboardInterrupt('validation cancelled by signal ' + str(signum))


def install_cancellation_handler():
    # SIGINT already becomes KeyboardInterrupt; turn ordinary termination into
    # the same cleanup/receipt path. SIGKILL cannot be intercepted.
    if hasattr(signal, 'SIGTERM'):
        return signal.signal(signal.SIGTERM, cancellation_handler)
    return None


def restore_cancellation_handler(previous):
    if previous is not None:
        signal.signal(signal.SIGTERM, previous)
