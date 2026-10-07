"""Measure a fixed authored retrieval rubric without models or ranking tuning.

This is a small diagnostic review, not a blinded benchmark or agent evaluation.
The rubric and corpus are written before indexing; all outcomes are preserved.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
from codeintel.lab.retrieval import index_repository, query_repository, serialize, verify_packet
_spec = importlib.util.spec_from_file_location('codeintel_claim_reproduction', ROOT / 'scripts/reproduce_claims.py')
_claims = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_claims)
source_identity = _claims.source_identity

SOURCES = {
    'a_notes.py': '# read_port is described in the settings module.\nDOCUMENTATION_ONLY = True\n',
    'file_settings.py': 'def read_port(config):\n    return int(config.get("port", 8080))\n',
    'account.py': 'def parse(text):\n    return {"account_id": int(text)}\n',
    'archive.py': 'def parse(text):\n    return {"archive_name": text.strip()}\n',
    'labels.ts': ('function normalizeLabel(label: string): string { return label.trim().toLowerCase(); }\n'
                  'export const formatLabel: (label: string) => string = label => normalizeLabel(label);\n'),
    'retail.py': 'TAX = 0.21\ndef price(amount):\n    return amount * (1 + TAX)\n',
    'wholesale.py': 'TAX = 0\ndef price(amount):\n    return amount * (1 + TAX)\n',
    'timeouts.py': 'DEFAULT_TIMEOUT = 30\n',
    'config_reader.py': 'from timeouts import DEFAULT_TIMEOUT\ndef read_timeout():\n    return DEFAULT_TIMEOUT\n',
    'validation.py': ('def validate_request(value):\n'
                      + ''.join(f'    value += {i}  # authored validation step\n' for i in range(65))
                      + '    if value < 0:\n        raise ValueError("final guard")\n    return value\n'),
}

# The questions/required literals are fixed before any result is inspected.
QUESTIONS = [
    {'id': 'file_prefix_disambiguation', 'question': 'Find the implementation that reads the configured port, not its comment.',
     'query': 'read_port', 'budget': 4096, 'limit': 1,
     'required': [('file_settings.py', 'return int(config.get("port", 8080))')]},
    {'id': 'qualified_name_collision', 'question': 'Inspect account parsing rather than archive parsing.',
     'query': 'account.parse', 'budget': 4096, 'limit': 2,
     'required': [('account.py', 'return {"account_id": int(text)}')]},
    {'id': 'typed_arrow_and_callee', 'question': 'See the typed formatter and the normalization it calls.',
     'query': 'formatLabel', 'budget': 4096, 'limit': 5,
     'required': [('labels.ts', 'label => normalizeLabel(label)'), ('labels.ts', 'label.trim().toLowerCase()')]},
    {'id': 'equal_body_distinct_constants', 'question': 'Determine why the two equal price bodies have different results.',
     'query': 'price', 'budget': 8192, 'limit': 5,
     'required': [('retail.py', 'TAX = 0.21'), ('wholesale.py', 'TAX = 0\n'),
                  ('retail.py', 'return amount * (1 + TAX)'), ('wholesale.py', 'return amount * (1 + TAX)')]},
    {'id': 'imported_configuration', 'question': 'Find the function and the timeout value it reads.',
     'query': 'read_timeout', 'budget': 4096, 'limit': 5,
     'required': [('config_reader.py', 'return DEFAULT_TIMEOUT'), ('timeouts.py', 'DEFAULT_TIMEOUT = 30')]},
    {'id': 'long_guard_small_budget', 'question': 'Inspect the final guard with a deliberately small packet.',
     'query': 'validate_request', 'budget': 2048, 'limit': 1,
     'required': [('validation.py', 'raise ValueError("final guard")')]},
    {'id': 'long_guard_large_budget', 'question': 'Inspect the same final guard with a larger packet.',
     'query': 'validate_request', 'budget': 8192, 'limit': 1,
     'required': [('validation.py', 'raise ValueError("final guard")')]},
    {'id': 'absent_symbol_control', 'question': 'Report absence for a name absent from this authored corpus.',
     'query': 'never_declared_control', 'budget': 4096, 'limit': 5, 'required': [], 'expect_empty': True},
]


def rubric_result(packet, question):
    missing = []
    for path, literal in question['required']:
        present = any(literal in row['source'] and path in {row['path'], *(a['path'] for a in row['aliases'])}
                      for row in packet['selected'])
        if not present:
            missing.append({'path': path, 'literal': literal})
    matched = not missing
    if question.get('expect_empty'):
        matched = packet['status'] == 'EMPTY' and not packet['selected']
    return {'required_literals_present': matched, 'missing_required_literals': missing,
            'meaning': 'Presence of declared literals only; not proof of an agent answer or dependency completeness.'}


def review(output: Path):
    output = output.resolve()
    if output.is_relative_to(ROOT):
        raise ValueError('Review output must be outside the source checkout')
    output.mkdir(parents=True, exist_ok=False)
    spec = {'schema': 'codeintel-authored-review-rubric-v1', 'questions': QUESTIONS,
            'source_sha256': {name: hashlib.sha256(raw.encode()).hexdigest() for name, raw in sorted(SOURCES.items())},
            'selection': 'Eight authored diagnostic questions; not random, blinded, held-out or independent agent tasks.',
            'literal_arm': 'Same indexed chunks, substring query and packet serializer; not ripgrep or whole-file search.',
            'direct_read': 'Oracle-assisted alternative: required file paths supplied by rubric; no packet/framing budget.',
            'rules': 'No ranking changes after observing this run. Keep failures and non-matching outcomes.'}
    raw_spec = (json.dumps(spec, ensure_ascii=False, indent=2) + '\n').encode()
    (output / 'rubric.json').write_bytes(raw_spec)
    rows = []
    with tempfile.TemporaryDirectory(prefix='codeintel-review-cases-') as temporary:
        repo, state = Path(temporary) / 'source', Path(temporary) / 'state'
        repo.mkdir()
        for name, raw in SOURCES.items():
            (repo / name).write_text(raw)
        index, _ = index_repository(repo, state)
        for number, question in enumerate(QUESTIONS):
            order = [False, True] if number % 2 == 0 else [True, False]
            arm_results = []
            for baseline in order:
                packet, _ = query_repository(repo, state, question['query'],
                    max_bytes=question['budget'], limit=question['limit'], baseline=baseline)
                verify_packet(repo, packet)
                arm_results.append({'arm': 'literal_scan' if baseline else 'ranked',
                                    **rubric_result(packet, question), 'packet': packet,
                                    'packet_bytes': len(serialize(packet)),
                                    'selected_body_bytes': sum(len(row['source'].encode()) for row in packet['selected']),
                                    'verified': True})
            paths = sorted({path for path, _ in question['required']})
            direct = {'required_paths_supplied': True, 'paths': paths,
                      'raw_file_bytes': sum(len(SOURCES[path].encode()) for path in paths),
                      'required_literals_present': all(literal in SOURCES[path] for path, literal in question['required']),
                      'not_budget_normalized': True}
            if question.get('expect_empty'):
                direct = {'required_paths_supplied': False, 'whole_authored_corpus_scanned': True,
                          'raw_file_bytes': sum(len(raw.encode()) for raw in SOURCES.values()),
                          'literal_name_absent': all(question['query'] not in raw for raw in SOURCES.values()),
                          'not_budget_normalized': True}
            rows.append({'id': question['id'], 'question': question['question'], 'query': question['query'],
                         'budget': question['budget'], 'fragment_limit': question['limit'],
                         'arms': arm_results, 'direct_read_alternative': direct})
    # A missing expected literal is a measured result, never relabeled PASS.
    summaries = {arm: {'questions': len(rows), 'required_literals_present': sum(
        result['required_literals_present'] for row in rows for result in row['arms'] if result['arm'] == arm)}
        for arm in ('ranked', 'literal_scan')}
    report = {'schema': 'codeintel-authored-review-results-v1', 'status': 'MEASURED',
              'rubric_sha256': hashlib.sha256(raw_spec).hexdigest(), 'corpus_files': len(SOURCES),
              'snapshot_sha256': index['snapshot_sha256'], **source_identity(ROOT),
              'rows': rows, 'summary': summaries, 'model_calls': 0, 'agent_answers_evaluated': 0,
              'limits': ['Authored diagnostic sample; no statistical or general retrieval superiority claim.',
                         'Matching declared literals is not a semantic correctness score.',
                         'Qualified-name queries and exact-symbol expansion are capabilities of the ranked arm; arms do not isolate ranking alone.',
                         'Direct file reads use oracle-supplied paths and omit protocol/selection overhead.',
                         'Packet bytes are not tokens, prices, latency or agent savings.']}
    (output / 'results.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    return report


def literal_file_packet(repo, query, max_bytes, limit=5):
    """Independent tiny-corpus control, not a new product reader or search API.

    Discovery paths, whole-file text, hashes and all framing share the same cap.
    The authored fixture has only ordinary supported files; no oracle is used.
    """
    matches, scanned = [], 0
    for path in sorted(repo.rglob('*')):
        if not path.is_file() or path.suffix not in ('.py', '.ts', '.js'):
            continue
        if path.is_symlink():
            raise ValueError('Diagnostic corpus must contain regular source files')
        raw = path.read_bytes()
        scanned += len(raw)
        text = raw.decode('utf-8')
        if query.casefold() in text.casefold():
            matches.append({'path': path.relative_to(repo).as_posix(), 'source': text,
                            'file_sha256': hashlib.sha256(raw).hexdigest(), 'aliases': []})
    packet = {'schema': 'literal-file-control-v1', 'status': 'EMPTY',
              'max_bytes': max_bytes, 'candidate_limit': 32, 'fragment_limit': limit,
              'discovery': [{key: row[key] for key in ('path', 'file_sha256')} for row in matches[:32]],
              'selected': [], 'omitted': {'candidate': max(0, len(matches) - 32), 'budget': 0, 'limit': 0}}
    if len(serialize(packet)) > max_bytes:
        raise ValueError('Discovery framing alone exceeds the predeclared diagnostic budget')
    for row in matches[:32]:
        if len(packet['selected']) == limit:
            packet['omitted']['limit'] += 1
            continue
        packet['selected'].append(row)
        packet['status'] = 'OK'
        if len(serialize(packet)) + 64 > max_bytes:
            packet['selected'].pop()
            packet['omitted']['budget'] += 1
    packet['status'] = 'OK' if packet['selected'] else ('BUDGET_EXHAUSTED' if matches else 'EMPTY')
    if len(serialize(packet)) > max_bytes:
        raise ValueError('Diagnostic packet exceeds complete-output budget')
    return packet, scanned


def review_handoff(output: Path):
    """Run the prospective frozen rubric once, preserving negative outcomes."""
    output = output.resolve()
    if output.is_relative_to(ROOT):
        raise ValueError('Review output must be outside the source checkout')
    output.mkdir(parents=True, exist_ok=False)
    rubric_bytes = (ROOT / 'docs/portfolio/diagnostics/context-handoff-rubric.json').read_bytes()
    rubric = json.loads(rubric_bytes)
    (output / 'rubric.json').write_bytes(rubric_bytes)
    rows, orders = [], ['ranked', 'literal_chunks', 'literal_files']
    with tempfile.TemporaryDirectory(prefix='codeintel-context-handoff-') as temporary:
        repo, state = Path(temporary) / 'source', Path(temporary) / 'state'
        repo.mkdir()
        for name, raw in rubric['sources'].items():
            path = repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(raw, encoding='utf-8')
        started = time.perf_counter()
        index, _ = index_repository(repo, state)
        cold_index_seconds = time.perf_counter() - started
        for number, question in enumerate(rubric['questions']):
            arms = []
            for arm in orders[number % 3:] + orders[:number % 3]:
                started = time.perf_counter()
                if arm == 'literal_files':
                    packet, scanned = literal_file_packet(repo, question['query'], question['max_bytes'], question['limit'])
                    verified = None  # This independent reader has no packet-verification contract.
                else:
                    packet, _ = query_repository(repo, state, question['query'],
                        max_bytes=question['max_bytes'], limit=question['limit'], baseline=arm == 'literal_chunks')
                    verify_packet(repo, packet)
                    scanned, verified = None, True
                arms.append({'arm': arm, **rubric_result(packet, question), 'packet': packet,
                             'complete_output_bytes': len(serialize(packet)), 'verified': verified,
                             'whole_file_scan_bytes': scanned, 'elapsed_seconds': time.perf_counter() - started})
            rows.append({'id': question['id'], 'query': question['query'], 'question': question['question'],
                         'budget': question['max_bytes'], 'absence_control': bool(question.get('expect_empty')),
                         'arms': arms})
    summary = {arm: {'questions': len(rows), 'required_literals_present': sum(
        item['required_literals_present'] for row in rows for item in row['arms'] if item['arm'] == arm),
        'substantive_questions': sum(not row['absence_control'] for row in rows),
        'substantive_matches': sum(item['required_literals_present'] for row in rows if not row['absence_control']
                                   for item in row['arms'] if item['arm'] == arm)} for arm in orders}
    report = {'schema': 'codeintel-context-handoff-results-v1', 'status': 'MEASURED',
              'rubric_sha256': hashlib.sha256(rubric_bytes).hexdigest(), **source_identity(ROOT),
              'corpus_files': len(rubric['sources']), 'corpus_source_bytes': sum(len(s.encode()) for s in rubric['sources'].values()),
              'snapshot_sha256': index['snapshot_sha256'], 'cold_index_seconds': cold_index_seconds,
              'rows': rows, 'summary': summary, 'model_calls': 0, 'agent_answers_evaluated': 0,
              'limits': [rubric['protocol']['bias'], rubric['protocol']['measurement'],
                         'Equal complete-output caps do not equalize search algorithms, preprocessing, CPU, or integrity metadata.',
                         'Literal file control independently reads complete files, with discovery framing counted; it is not ripgrep.',
                         'No ranking changes or corpus/rubric changes are permitted after these results.',
                         'VERIFIED and witness presence do not prove that an agent can answer correctly.']}
    (output / 'results.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--handoff', action='store_true', help='Run the prospectively frozen equal-output-budget diagnostic')
    args = parser.parse_args()
    result = (review_handoff if args.handoff else review)(args.output)
    print(json.dumps({'status': result['status'], 'summary': result['summary'], 'model_calls': 0}))
