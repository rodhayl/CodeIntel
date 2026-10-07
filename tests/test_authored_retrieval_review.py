"""The authored review reports misses honestly and preserves a predeclared rubric."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('review_cases', ROOT / 'scripts/review_retrieval_cases.py')
reviewer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reviewer)


def test_rubric_reports_missing_literals_without_calling_them_pass():
    question = {'required': [('source.py', 'return useful()')]}
    result = reviewer.rubric_result({'status': 'EMPTY', 'selected': []}, question)
    assert result['required_literals_present'] is False
    assert result['missing_required_literals'] == [{'path': 'source.py', 'literal': 'return useful()'}]


def test_alias_matches_body_but_never_invents_module_constants():
    packet = {'status': 'OK', 'selected': [{'path': 'one.py', 'source': 'return amount * TAX',
                                          'aliases': [{'path': 'two.py'}]}]}
    question = {'required': [('two.py', 'return amount * TAX'), ('two.py', 'TAX = 0.21')]}
    result = reviewer.rubric_result(packet, question)
    assert result['required_literals_present'] is False
    assert result['missing_required_literals'] == [{'path': 'two.py', 'literal': 'TAX = 0.21'}]


def test_fixed_review_preserves_all_outcomes_and_separates_reader_alternative(tmp_path, monkeypatch):
    original = reviewer.query_repository
    output = tmp_path / 'review'
    def query(*args, **kwargs):
        assert (output / 'rubric.json').is_file(), 'Rubric must precede result inspection'
        return original(*args, **kwargs)
    monkeypatch.setattr(reviewer, 'query_repository', query)
    result = reviewer.review(output)
    assert result['status'] == 'MEASURED'
    assert result['model_calls'] == result['agent_answers_evaluated'] == 0
    assert result['rubric_sha256'] == hashlib.sha256((output / 'rubric.json').read_bytes()).hexdigest()
    assert len(result['rows']) == 8
    rubric = json.loads((output / 'rubric.json').read_text())
    assert [row['id'] for row in result['rows']] == [q['id'] for q in rubric['questions']]
    for row in result['rows']:
        assert {r['arm'] for r in row['arms']} == {'ranked', 'literal_scan'}
        for arm in row['arms']:
            assert arm['verified'] and arm['packet_bytes'] <= row['budget']
            assert isinstance(arm['required_literals_present'], bool)
        assert row['direct_read_alternative']['not_budget_normalized'] is True
    for arm, summary in result['summary'].items():
        assert summary['questions'] == 8
        assert summary['required_literals_present'] == sum(r['required_literals_present']
            for row in result['rows'] for r in row['arms'] if r['arm'] == arm)
    # A second run cannot replace an earlier measured result.
    with pytest.raises(FileExistsError):
        reviewer.review(output)


def test_literal_file_control_counts_discovery_and_whole_file_framing(tmp_path):
    (tmp_path / 'source.py').write_text('CAP = 31\ndef retry():\n    return CAP\n')
    packet, scanned = reviewer.literal_file_packet(tmp_path, 'retry', 1024)
    assert packet['status'] == 'OK'
    assert len(reviewer.serialize(packet)) <= 1024
    assert packet['discovery'][0]['path'] == 'source.py'
    assert 'CAP = 31' in packet['selected'][0]['source']
    assert scanned == (tmp_path / 'source.py').stat().st_size
    (tmp_path / 'source.py').write_text('CAP = 31\n' + '# padding\n' * 150 + 'def retry(): return CAP\n')
    packet, _ = reviewer.literal_file_packet(tmp_path, 'retry', 1024)
    assert packet['status'] == 'BUDGET_EXHAUSTED'
    assert packet['selected'] == []
    assert packet['omitted']['budget'] == 1


def test_handoff_rubric_freeze_has_unique_cases_and_source_identities():
    path = ROOT / 'docs/portfolio/diagnostics/context-handoff-rubric.json'
    raw = path.read_bytes()
    # Frozen before any output; changing the experiment requires a new artifact.
    assert hashlib.sha256(raw).hexdigest() == '7a69fa57d554dbd7228e663b3cab649249efd9c0b2d58a9d2a6b2b6b51c0c966'
    rubric = json.loads(raw)
    assert len({q['id'] for q in rubric['questions']}) == 8
    assert {name: hashlib.sha256(text.encode()).hexdigest() for name, text in rubric['sources'].items()} == rubric['source_sha256']
    assert all(q['max_bytes'] in (2048, 4096, 8192) for q in rubric['questions'])
