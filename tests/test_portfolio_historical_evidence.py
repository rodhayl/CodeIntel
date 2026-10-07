"""Audit the sanitized evidence arithmetic and narrative boundary, not model quality."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_historical_denominators_counterexamples_and_identity_are_retained():
    h=json.loads((ROOT/'docs/portfolio/HISTORICAL_EVIDENCE.json').read_text())
    for key, totals, pairs in [('agy_precompiled',(1700615,953118),7),('codex_gateway',(504945,287417),3)]:
        d=h[key]
        assert len(d['rows'])==pairs*2
        assert tuple(sum(r['tokens'] for r in d['rows'] if r['arm']==arm) for arm in ('control','treatment'))==totals
        assert d['tasks']==d['pairs']==pairs and d['repeats_per_task']==1
        assert abs(d['treatment_change_percent']-(totals[1]/totals[0]-1)*100)<1e-10
    agy=h['agy_precompiled'];rows=agy['rows']
    assert agy['control_first_every_pair']
    assert next(r['tokens'] for r in rows if r['task']=='task_4' and r['arm']=='treatment') > next(r['tokens'] for r in rows if r['task']=='task_4' and r['arm']=='control')
    assert h['client_matrix']['status']=='PARTIAL'
    assert h['october_adverse']['source_commit'] is None
    assert h['october_adverse']['target_repository']=='HTTPX'
    assert h['target_repository']=='cvMadeEasy'
    assert h['current_runtime_agent_savings_proven'] is False


def test_public_ledger_omits_conversations_paths_and_foreign_source():
    raw=(ROOT/'docs/portfolio/HISTORICAL_EVIDENCE.json').read_text()
    for forbidden in ('conversation_id','transcript_full','/root/','/home/','C:\\\\Users','"response":','"prompt":'):
        assert forbidden not in raw
    h=json.loads(raw)
    assert all(h['historical_evidence_commit'] in x['url'] for x in h['private_source_references'])


def test_use_case_script_reproduces_metadata_tradeoff_and_fallback():
    spec=importlib.util.spec_from_file_location('use_cases',ROOT/'scripts/reproduce_use_cases.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    report=module.reproduce()
    assert report['status']=='PASS' and report['model_calls']==0
    case=report['shared_body_different_constants']
    assert case['alias_coverage_scope_added_bytes'] > 0
    assert case['whole_file_reader_bytes'] < case['packet_bytes']
    assert report['long_symbol']['complete_packet']['selected'][0]['coverage']['complete']
    assert not report['long_symbol']['larger_symbol_partial_packet']['selected'][0]['coverage']['complete']


def test_reported_counters_keep_provider_cache_semantics_separate():
    h=json.loads((ROOT/'docs/portfolio/HISTORICAL_EVIDENCE.json').read_text())
    assert all(r['tokens']==r['input_tokens']+r['output_tokens'] for r in h['agy_precompiled']['rows'])
    assert all('cache_read_tokens' in r for r in h['agy_precompiled']['rows'])
    assert all(r['cached_input_tokens']<=r['input_tokens'] for r in h['codex_gateway']['rows'])
    assert 'separately' in h['agy_precompiled']['reported_counter_definition']
    assert 'included' in h['codex_gateway']['reported_counter_definition']


def test_october_report_arithmetic_keeps_latency_commands_and_preliminary_failure_separate():
    historical = json.loads((ROOT / 'docs/portfolio/HISTORICAL_EVIDENCE.json').read_text())
    adverse = historical['october_adverse']
    rows = adverse['report_rows']
    assert len(rows) == 4 and adverse['pairs'] == 2 and adverse['tasks'] == 1
    for arm, stored_arm in [('control', 'baseline'), ('treatment', 'treatment')]:
        arm_rows = [row for row in rows if row['arm'] == stored_arm]
        assert len(arm_rows) == 2
        assert sum(row['total_tokens'] for row in arm_rows) == adverse[f'{arm}_tokens']
        assert sum(row['command_calls'] for row in arm_rows) == adverse['command_calls'][arm]
        assert abs(sum(row['elapsed_seconds'] for row in arm_rows)
                   - adverse['recorded_elapsed_seconds'][arm]) < 1e-9
    assert adverse['preliminary_capture_failure']['included_in_four_run_ab_totals'] is False
    assert adverse['source_commit'] is None
    assert adverse['report_git_blob'] == 'd48f0308bb9f73576b1241c5bb320d40eb885779'
    assert 'not a fresh raw-log recount' in adverse['verification_scope']


def test_retired_platform_receipts_keep_skip_counts_and_candidate_identity():
    historical = json.loads((ROOT / 'docs/portfolio/HISTORICAL_EVIDENCE.json').read_text())
    matrix = historical['client_matrix']
    receipts = {row['kind']: row for row in matrix['source_receipts']}
    for platform, passed, skipped in [('linux', 2773, 7), ('windows', 2422, 352)]:
        row = receipts[f'platform/{platform}/result.json']
        assert row['source']['commit'] == matrix['candidate_commit']
        assert row['source']['clean'] is True
        counts = row['counts']['regression']
        assert counts['passed'] == passed and counts['skipped'] == skipped
        assert counts['tests'] == passed + skipped
    assert 'WSL2' in receipts['platform/linux/result.json']['platform']['platform']
    assert matrix['status'] == 'PARTIAL'
    assert historical['current_runtime_agent_savings_proven'] is False
