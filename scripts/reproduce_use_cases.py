"""Reproduce the two reviewed retrieval failures on disposable authored source.

No model calls or external repositories. Native whole-file byte counts are a
simple reader alternative, not an agent-token or protocol-normalized baseline.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))

from codeintel.lab.retrieval import LabError, index_repository, query_repository, serialize, verify_packet


def reproduce() -> dict:
    with tempfile.TemporaryDirectory(prefix='codeintel-use-cases-') as work:
        root = Path(work)
        repo = root / 'source'; repo.mkdir()
        body = 'def price(amount):\n    return amount * (1 + TAX)\n'
        for name, constant in [('gross.py', '.21'), ('net.py', '0')]:
            (repo / name).write_text('TAX = ' + constant + '\n' + body)
        index_repository(repo, root / 'state')
        packet, receipt = query_repository(repo, root / 'state', 'price', max_bytes=8192, limit=20)
        verify_packet(repo, packet)
        row = next(row for row in packet['selected'] if 'def price' in row['source'])
        assert {row['path'], *(a['path'] for a in row['aliases'])} == {'gross.py', 'net.py'}
        # Precisely isolate metadata cost, not an old-engine performance result.
        projection = deepcopy(packet)
        projection['schema'] = 'codeintel-lab-packet-v1'
        projection.pop('scope')
        for item in projection['selected']:
            item.pop('coverage'); item.pop('aliases')
        overhead = len(serialize(packet)) - len(serialize(projection))
        whole_file_bytes = sum(len(p.read_bytes()) for p in repo.iterdir())
        (repo / 'net.py').write_text('TAX = .10\n' + body)
        try:
            verify_packet(repo, packet)
        except LabError as exc:
            stale_code = exc.code
        else:
            raise AssertionError('Changed alias incorrectly verified')
        assert stale_code == 'STALE_SOURCE'
        after, _ = query_repository(repo, root / 'state', 'price', max_bytes=8192, limit=20)
        verify_packet(repo, after)
        alias_result = {'packet_before': packet, 'packet_after': after,
                        'packet_bytes': receipt['packet_bytes'],
                        'literal_bytes_stored_once': receipt['source_bytes'],
                        'metadata_bytes': receipt['packet_bytes'] - receipt['source_bytes'],
                        'v1_shape_projection_bytes': len(serialize(projection)),
                        'alias_coverage_scope_added_bytes': overhead,
                        'whole_file_reader_bytes': whole_file_bytes,
                        'reader_example': 'Read both gross.py and net.py, including their TAX assignments.',
                        'expected_price_at_100_before': {'gross': 121, 'net': 100},
                        'constant_only_mutation_result': stale_code,
                        'limits': 'Authored fixture. A v1-shape projection isolates added metadata, not old-engine latency or agent performance. Whole-file bytes exclude shell/framing overhead.'}
        long_repo = root / 'long'; long_repo.mkdir()
        source = ('def validate(value):\n' + ''.join(
            f'    value += {i}  # work before the final guard\n' for i in range(65)) +
            '    if value < 0:\n        raise ValueError("final guard")\n    return value\n')
        (long_repo / 'rules.py').write_text(source)
        index_repository(long_repo, root / 'long-state')
        complete, complete_receipt = query_repository(long_repo, root / 'long-state', 'validate', max_bytes=8192, limit=1)
        partial, partial_receipt = query_repository(long_repo, root / 'long-state', 'validate', max_bytes=4096, limit=1)
        verify_packet(long_repo, complete); verify_packet(long_repo, partial)
        assert complete['selected'][0]['coverage']['complete']
        assert 'final guard' in complete['selected'][0]['source']
        # Use a deliberately larger source so the fallback is certain, independent
        # of incidental metadata shrinkage on the complete-symbol case.
        huge = source.replace('    if value < 0:', '    # ' + 'larger input ' * 900 + '\n    if value < 0:')
        (long_repo / 'rules.py').write_text(huge)
        fallback, fallback_receipt = query_repository(long_repo, root / 'long-state', 'validate', max_bytes=4096, limit=1)
        verify_packet(long_repo, fallback)
        assert fallback['selected'] and not fallback['selected'][0]['coverage']['complete']
        long_result = {'complete_packet': complete, 'complete_packet_bytes': complete_receipt['packet_bytes'],
                       'whole_file_reader_bytes': len(source.encode()),
                       'larger_symbol_partial_packet': fallback,
                       'partial_packet_bytes': fallback_receipt['packet_bytes'],
                       'fallback': 'Read rules.py at coverage.symbol_span or read the complete file. Re-query after any edit.',
                       'limits': 'Known symbol on authored Python. Complete means that syntax range, not dependencies or task correctness.'}
    runtime = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
               for p in sorted((ROOT / 'codeintel').rglob('*.py'))}
    return {'schema': 'codeintel-use-cases-v1', 'status': 'PASS', 'model_calls': 0,
            'runtime_sha256': runtime, 'shared_body_different_constants': alias_result,
            'long_symbol': long_result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = reproduce()
    with args.output.open('x') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2); stream.write('\n')
    print(json.dumps({'status': report['status'], 'model_calls': 0,
                      'alias_packet_bytes': report['shared_body_different_constants']['packet_bytes'],
                      'alias_coverage_scope_added_bytes': report['shared_body_different_constants']['alias_coverage_scope_added_bytes'],
                      'complete_symbol_packet_bytes': report['long_symbol']['complete_packet_bytes']}))


if __name__ == '__main__':
    main()
