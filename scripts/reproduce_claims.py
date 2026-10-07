"""Reproduce the bounded claims on synthetic data and the maintained runtime.

No inference, network, external repository or autonomous code change. Writes only
inside a new explicitly requested evidence workspace, plus its temporary state.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import shutil
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))


def source_identity(root: Path) -> dict:
    """Bind identity to this root, never to an unrelated enclosing Git repository."""
    runtime_files = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in sorted((root / 'codeintel').rglob('*'))
                     if p.is_file() and p.suffix in ('.py', '.js', '.ts')}
    runtime = {name: digest for name, digest in runtime_files.items() if name.endswith('.py')}
    manifest_path = root / 'SNAPSHOT_MANIFEST.json'
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text())
        listed = {n: h for n, h in manifest.get('files', {}).items()
                  if n.startswith('codeintel/') and Path(n).suffix in ('.py', '.js', '.ts')}
        matches = listed == runtime_files
        return {'source_commit': manifest.get('source_commit') if matches else None,
                'source_dirty': not matches, 'identity_basis': 'snapshot manifest + current runtime hashes',
                'runtime_sha256': runtime, 'runtime_files_sha256': runtime_files}
    if (root / '.git').exists():
        try:
            top = subprocess.check_output(['git', 'rev-parse', '--show-toplevel'], cwd=root, text=True, timeout=5).strip()
            if Path(top).resolve() == root.resolve():
                return {'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True, timeout=5).strip(),
                        'source_dirty': bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=root, timeout=5)),
                        'identity_basis': 'this root Git checkout + current runtime hashes', 'runtime_sha256': runtime, 'runtime_files_sha256': runtime_files}
        except (OSError, subprocess.SubprocessError):
            pass
    return {'source_commit': None, 'source_dirty': None,
            'identity_basis': 'current runtime hashes only; no bound commit available', 'runtime_sha256': runtime, 'runtime_files_sha256': runtime_files}


def reproduce(out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=False)
    # Block the transport primitives during actual experiments, not only by configuration.
    def forbidden(*args, **kwargs):
        raise AssertionError('This claim run must not use the network')
    socket.socket.connect = socket.socket.connect_ex = socket.create_connection = forbidden
    from codeintel.lab.scenarios import FIXTURE, run_demo, evaluate
    from codeintel.lab.retrieval import index_repository, query_repository, verify_packet, serialize, digest
    from codeintel.service import create_default_service
    demo = run_demo(out / 'demo-work')
    evaluation = evaluate()
    (out / 'demo.json').write_bytes(serialize(demo))
    (out / 'evaluation.json').write_bytes(serialize(evaluation))
    rows = []
    def claim(id, text, passed, evidence, limit):
        rows.append({'id': id, 'claim': text, 'status': 'REPRODUCED' if passed else 'FAIL', 'evidence': evidence, 'limit': limit})
    old, fresh = demo['steps'][2]['packet'], demo['steps'][5]['packet']
    claim('C01','Python, TypeScript and JavaScript symbol retrieval', all(r['correct'] for r in evaluation['rows']), 'evaluation.json: rows', 'Four authored cases, not a held-out retrieval benchmark')
    claim('C02','Literal path, span and SHA-256 provenance', all(digest(r['source'].encode()) == r['source_sha256'] for r in old['selected'] + fresh['selected']), 'demo.json: query packets', 'Selected source only; hashes do not authenticate a hostile producer')
    claim('C03','Complete serialized JSON fits its byte budget', all(c['pass'] for c in evaluation['checks'] if c['scenario'].startswith('budget_')), 'evaluation.json: budget_1024/2048/4096/8192', 'Bytes include metadata/newline; they are not tokens or costs')
    claim('C04','Old selected source is rejected after scripted file change', demo['steps'][4]['result'] == 'STALE_SOURCE' and fresh['refreshed'], 'demo.json: modify, verify_old_packet, query_after_modify', 'The demo supplies the edit; no agent diagnoses or fixes the bug')
    claim('C05','Missing index has structured recovery', demo['steps'][0]['result'] == 'INDEX_REQUIRED', 'demo.json: query_before_index', 'Expected CLI failures are covered by lab regression tests')
    repo, state = out / 'scope-source', out / 'scope-state'
    shutil.copytree(FIXTURE, repo, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    original = {p.name: digest(p.read_bytes()) for p in repo.iterdir() if p.is_file()}
    index_repository(repo, state)
    first, audit = query_repository(repo, state, 'reserve_seats')
    second, _ = query_repository(repo, state, 'reserve_seats')
    claim('C06','Store exact repeated text once with verifiable location aliases', first['omitted']['duplicate'] == second['omitted']['duplicate'] == 1 and first['selected'] == second['selected'], 'scope.json: repeated query packets', 'Aliases are equal text, not semantic equivalence; candidate-bounded, no cross-query memory')
    current = {p.name: digest(p.read_bytes()) for p in repo.iterdir() if p.is_file()}
    forbidden_receipt = subprocess.run([sys.executable, '-m', 'codeintel.lab.cli', 'query',
        '--repo', str(repo), '--state-dir', str(state), '--record', str(repo/'receipt.py'), 'reserve_seats'],
        cwd=ROOT, capture_output=True, timeout=20)
    receipt_error = json.loads(forbidden_receipt.stderr)
    unchanged = {p.name: digest(p.read_bytes()) for p in repo.iterdir() if p.is_file()} == original
    claim('C07','Index/query leave inspected source unchanged and reject in-source receipts',
          current == original and unchanged and forbidden_receipt.returncode == 2 and not forbidden_receipt.stdout
          and receipt_error['code'] == 'RECORD_INSIDE_REPOSITORY',
          'scope.json: before/after digests and forbidden_receipt_error',
          'State and optional outputs are written outside inspected source')
    empty, _ = query_repository(repo, state, 'newly_added_symbol')
    (repo / 'new.py').write_text('def newly_added_symbol():\n    return 42\n')
    verify_packet(repo, first)
    verify_packet(repo, empty)
    updated, _ = query_repository(repo, state, 'newly_added_symbol')
    claim('C08','Verification is selected-source checking, not query completeness', empty['status'] == 'EMPTY' and updated['refreshed'] and bool(updated['selected']), 'scope.json: old_empty and new_query', 'An old EMPTY packet can still verify after a new matching file is added')
    claim('C09','Receipts omit query/source text and preserve unknown agent metrics', 'source' not in audit['selected'][0] and audit['tokens'] is audit['cost'] is audit['agent_received'] is None, 'scope.json: receipt', 'Emission is not agent receipt/retention; query hash remains metadata')
    with create_default_service(str(repo), str(out / 'rebuild-state')) as service:
        a = service.reindex()
        (repo / 'new.py').write_text('def newly_added_symbol():\n    return 43\n')
        b = service.reindex()
        claim('C10','Source changes rebuild a complete immutable generation', a['is_incremental'] is b['is_incremental'] is False and a['generation_id'] != b['generation_id'], 'scope.json: rebuild_results', 'No incremental speedup or scale/latency claim')
    (out / 'scope.json').write_text(json.dumps({'repeated_packets':[first,second],'before':original,'after':current,'old_empty':empty,'new_query':updated,'receipt':audit,'rebuild_results':[a,b],'forbidden_receipt_error':receipt_error},indent=2)+'\n')
    index, _ = index_repository(ROOT / 'codeintel', out / 'real-state')
    real, real_audit = query_repository(ROOT / 'codeintel', out / 'real-state', 'verify_packet', max_bytes=4096, limit=1)
    verify_packet(ROOT / 'codeintel', real)
    claim('C11','Exact-symbol lookup works on maintained project source', real['selected'][0]['path'] == 'lab/retrieval.py' and 'STALE_SOURCE' in real['selected'][0]['source'], 'real-source.json', 'One authored lookup; not blind, comparative or proof of general relevance')
    (out / 'real-source.json').write_text(json.dumps({'index':index,'packet':real,'receipt':real_audit},indent=2)+'\n')
    groups = {}
    for row in evaluation['rows']:
        key=(row['scenario'],row['repetition']);groups.setdefault(key,[]).append(row['telemetry']['config'])
    claim('C12','Literal baseline shares the index and bounded packet contract', len(groups)==12 and all(len(g)==2 and g[0]['packet_bytes']==g[1]['packet_bytes'] for g in groups.values()), 'evaluation.json and scenarios.py', 'Ranked exact symbols may expand; baseline scans initial chunks. Not ripgrep, whole-file control or isolated ranking superiority')
    claim('C13','Demo/evaluation execute with in-process sockets blocked and no model imports', demo['status']==evaluation['status']=='PASS' and not any(m in sys.modules for m in ('numpy','torch','llama_cpp','transformers')), 'in-process socket connect/connect_ex/create_connection blocked; imports checked', 'Does not prove OS/subprocess-wide network isolation or downstream agent behavior')
    history=json.loads((ROOT/'docs/reports/portfolio-readiness/public/historical-measurements.json').read_text())
    base=history['native_ab']['totals']['baseline'];treatment=history['native_ab']['totals']['treatment']
    claim('H01','Historical exploratory token result was adverse', round((treatment/base-1)*100,2)==69.78, 'historical-measurements.json: retained denominators and trace digests', 'Historical one-task/two-pair observation; not rerun or generalized')
    historical = json.loads((ROOT/'docs/portfolio/HISTORICAL_EVIDENCE.json').read_text())
    for cid, key, totals in [('H02', 'agy_precompiled', (1700615,953118)), ('H03','codex_gateway',(504945,287417))]:
        h=historical[key]
        observed=tuple(sum(r['tokens'] for r in h['rows'] if r['arm']==arm) for arm in ('control','treatment'))
        claim(cid,'Recompute retained historical totals: '+key, observed==totals and observed==(h['control_tokens'],h['treatment_tokens']), 'HISTORICAL_EVIDENCE.json: '+key, 'Arithmetic over sanitized historical counts; no new model execution or blinded quality audit')
    spec = importlib.util.spec_from_file_location('codeintel_use_case_reproduction', ROOT / 'scripts/reproduce_use_cases.py')
    use_case_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(use_case_module)
    use_cases = use_case_module.reproduce()
    (out/'use-cases.json').write_text(json.dumps(use_cases,indent=2)+'\n')
    claim('C14','Retain aliases and expose exact-symbol coverage/fallback', use_cases['status']=='PASS', 'use-cases.json; tests/test_lab_alias_symbol_coverage.py', 'Authored cases. Known literal ranges, not dependency completeness or agent value')
    report={'schema':'codeintel-claim-reproduction-v1','status':'PASS' if all(r['status']=='REPRODUCED' for r in rows) else 'FAIL','claims':rows,**source_identity(ROOT),'model_calls':0,'in_process_socket_primitives_blocked':True,'agent_performance_validated':False,'software_production_ready':False}
    (out / 'claims.json').write_text(json.dumps(report,indent=2)+'\n')
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    report=reproduce(args.output.resolve());print(json.dumps({'status':report['status'],'claims':len(report['claims']),'source_dirty':report['source_dirty']}))
    if report['status']!='PASS':raise SystemExit(1)

if __name__=='__main__':main()
