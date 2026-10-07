"""One fail-closed offline gate for a clean candidate; no models."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Standalone maintenance scripts intentionally use their own source runtime.
# Importing this module during installed pytest must not alter runtime resolution.
if __name__ == '__main__':
    sys.path.insert(0, str(ROOT))
_spec = importlib.util.spec_from_file_location('codeintel_validation_contract', Path(__file__).with_name('validation_contract.py'))
contract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(contract)
GATE_OVERHEAD_SECONDS = 60


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--suite-timeout', type=contract.suite_timeout, default=contract.DEFAULT_SUITE_SECONDS,
                        help='Full-suite seconds, default 360, maximum 900; collection has its own 30-second bound')
    args = parser.parse_args(argv)
    out = args.output.resolve()
    if out.is_relative_to(ROOT):
        raise SystemExit('Validation output must be outside the checkout')
    out.mkdir(parents=True, exist_ok=False)
    previous_handler = contract.install_cancellation_handler()
    runner = contract.StageRunner(out, cwd=ROOT,
        env=dict(os.environ, CODEINTEL_DISABLE_DENSE='1', CODEINTEL_DISABLE_SCIP='1'),
        replacements=[(str(ROOT), 'CHECKOUT'), (sys.prefix, 'VALIDATION_VENV'), (str(out), 'EVIDENCE_OUTPUT')],
        receipt_base={'schema': 'portfolio-consolidation-v3'}, total_seconds=args.suite_timeout + GATE_OVERHEAD_SECONDS)
    started = time.perf_counter()
    reasons, report, sha, source_hashes = [], {}, None, {}
    clean_before = clean_after = cancelled = False
    stage = 'source-preflight'
    try:
        contract.require(not runner.run('git-clean-before', ['git', 'status', '--porcelain'], timeout=5).stdout,
                         'Candidate must be clean before consolidation')
        clean_before = True
        contract.require((ROOT / 'tests').is_dir(), 'Missing retained test directory')
        sha = runner.run('git-head-before', ['git', 'rev-parse', 'HEAD'], timeout=5).stdout.decode().strip()
        tracked = runner.run('git-tracked-files', ['git', 'ls-files', '-z'], timeout=5).stdout.decode().rstrip('\0').split('\0')
        source_hashes = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in tracked}
        stage = 'source-tests'
        report = contract.run_pytest_suite(runner, python=sys.executable, source=ROOT,
            runtime_root=ROOT / 'codeintel', mode='source', junit_name='consolidated.junit.xml', timeout_seconds=args.suite_timeout)
    except (KeyboardInterrupt, Exception) as exc:
        cancelled = isinstance(exc, KeyboardInterrupt)
        reasons.append(runner.clean(str(exc) or 'validation cancelled'))
        runner.fail(stage, exc)
    finally:
        if clean_before and sha:
            try:
                after_sha = runner.run('git-head-after', ['git', 'rev-parse', 'HEAD'], timeout=5).stdout.decode().strip()
                dirty = runner.run('git-clean-after', ['git', 'status', '--porcelain'], timeout=5).stdout
                clean_after = not dirty and after_sha == sha and all(
                    hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest
                    for name, digest in source_hashes.items())
            except (KeyboardInterrupt, Exception):
                clean_after = False
        if not clean_after:
            reasons.append('source changed or could not be verified after execution')
        receipt = {'schema': 'portfolio-consolidation-v3', 'status': 'FAIL' if reasons else 'PASS',
                   'tested_source_commit': sha, 'source_file_sha256': source_hashes,
                   'clean_before': clean_before, 'clean_after': clean_after,
                   'deadline_seconds': args.suite_timeout + GATE_OVERHEAD_SECONDS,
                   'suite_deadline_seconds': args.suite_timeout, 'collection_deadline_seconds': contract.COLLECTION_SECONDS, 'elapsed_seconds': time.perf_counter() - started,
                   'exit_code': 0 if not reasons else None,
                   'timed_out': any(row['timed_out'] for row in runner.runs),
                   'cancelled': cancelled or any(row['failure_kind'] == 'cancelled' for row in runner.runs),
                   'failure_reasons': reasons, 'runs': runner.runs, **report, 'model_calls': 0,
                   'scope': 'independent complete collection equals one full-suite collection and JUnit; source runtime deliberate',
                   'exclusions': ['model inference', 'retired integrations', 'Windows/macOS hosts',
                                  'agent-quality evaluation', 'detached subprocess sessions']}
        (out / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
        contract.restore_cancellation_handler(previous_handler)
    print(json.dumps({key: receipt.get(key) for key in ('status', 'tested_source_commit', 'clean_before', 'clean_after', 'junit', 'failure_reasons')}))
    raise SystemExit(1 if reasons else 0)


if __name__ == '__main__':
    main()
