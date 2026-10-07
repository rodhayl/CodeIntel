"""Adversarial regression for equal text in distinct contexts and split symbols."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from codeintel.lab.retrieval import (LabError, SCOPE, index_repository,
                                     query_repository, serialize, verify_packet)


def indexed(tmp_path, sources):
    repo, state = tmp_path / 'repo', tmp_path / 'state'
    repo.mkdir()
    for name, source in sources.items():
        (repo / name).write_text(source)
    index_repository(repo, state)
    return repo, state


def locations(packet):
    return [item for row in packet['selected'] for item in [row, *row['aliases']]]


def test_identical_body_keeps_module_provenance_without_equating_constants(tmp_path):
    body = 'def price(amount):\n    return amount * (1 + TAX)\n'
    repo, state = indexed(tmp_path, {'gross.py': 'TAX = .21\n' + body,
                                    'net.py': 'TAX = 0\n' + body})
    packet, audit = query_repository(repo, state, 'price', max_bytes=8192, limit=20)
    verify_packet(repo, packet)
    row = next(r for r in packet['selected'] if 'def price' in r['source'])
    assert {row['path'], *(a['path'] for a in row['aliases'])} == {'gross.py', 'net.py'}
    assert row['coverage']['complete'] and row['aliases'][0]['coverage']['complete']
    assert 'not imports/constants' in packet['scope']
    assert 'not exhaustive' in packet['scope']
    assert 'TAX =' not in row['source']
    assert audit['duplicate_content_bytes'] == 0
    assert audit['packet_bytes'] == len(serialize(packet)) <= 8192


@pytest.mark.parametrize('path', ['gross.py', 'net.py'])
@pytest.mark.parametrize('operation', ['change_constant', 'change_body', 'delete'])
def test_every_alias_is_bound_to_its_current_file(tmp_path, path, operation):
    body = 'def price(amount):\n    return amount * (1 + TAX)\n'
    repo, state = indexed(tmp_path, {'gross.py': 'TAX = .21\n' + body,
                                    'net.py': 'TAX = 0\n' + body})
    packet, _ = query_repository(repo, state, 'price', max_bytes=8192)
    if operation == 'delete':
        (repo / path).unlink()
    elif operation == 'change_constant':
        (repo / path).write_text('TAX = .99\n' + body)
    else:
        (repo / path).write_text('TAX = 0\ndef price(amount):\n    return amount\n')
    with pytest.raises(LabError, match='SOURCE_UNAVAILABLE|STALE_SOURCE'):
        verify_packet(repo, packet)


@pytest.mark.parametrize('budget', [1024, 2048, 4096, 8192])
def test_many_aliases_keep_complete_json_budget_and_explicit_bound(tmp_path, budget):
    repo, state = indexed(tmp_path, {f'module_{i:02}.py': f'TAX = {i}\ndef price(a):\n    return a * TAX\n'
                                    for i in range(38)})
    packet, _ = query_repository(repo, state, 'price', max_bytes=budget, limit=20)
    verify_packet(repo, packet)
    assert len(serialize(packet)) <= budget
    assert len(locations(packet)) <= 32
    assert 'Candidates capped at 32' in packet['scope']
    assert len(locations(packet)) < 38
    if len(locations(packet)) < 32:
        assert packet['omitted']['budget']


def long_source(name='validate', lines=65):
    return f'def {name}(value):\n' + ''.join(f'    value += {i}  # ordinary work before final check\n' for i in range(lines)) + '    if value < 0:\n        raise ValueError("final guard")\n    return value\n'


def test_long_exact_symbol_expands_past_prologue_when_budget_permits(tmp_path):
    source = long_source()
    repo, state = indexed(tmp_path, {'rules.py': source})
    packet, _ = query_repository(repo, state, 'validate', max_bytes=8192)
    verify_packet(repo, packet)
    selected = packet['selected']
    assert len(selected) == 1
    assert 'final guard' in selected[0]['source']
    assert selected[0]['source'] == source.rstrip('\n')
    assert selected[0]['coverage']['complete'] is True
    assert selected[0]['span'] == selected[0]['coverage']['symbol_span']


def test_too_long_symbol_reports_partial_range_and_file_fallback(tmp_path):
    repo, state = indexed(tmp_path, {'rules.py': long_source(lines=250)})
    packet, _ = query_repository(repo, state, 'validate', max_bytes=4096, limit=1)
    verify_packet(repo, packet)
    row = packet['selected'][0]
    assert row['coverage']['complete'] is False
    assert row['coverage']['symbol_span'][2] > row['span'][2]
    assert 'read the recorded range or full file' in packet['scope']
    assert packet['omitted']['limit'] or packet['omitted']['budget']


def test_symbol_with_children_covers_nested_logic_and_utf8(tmp_path):
    source = ('class Price:\n    """' + 'á' * 1000 + '"""\n'
              '    def calculate(self, amount):\n        return amount * 1.21\n')
    repo, state = indexed(tmp_path, {'prices.py': source})
    packet, _ = query_repository(repo, state, 'Price', max_bytes=8192)
    verify_packet(repo, packet)
    row = packet['selected'][0]
    assert row['coverage']['complete'] is True
    assert 'return amount * 1.21' in row['source']
    assert row['source'].encode() == source.rstrip('\n').encode()


def test_duplicate_symbol_names_preserve_distinct_complete_bodies(tmp_path):
    repo, state = indexed(tmp_path, {'a.py': long_source(lines=34),
                                    'b.py': long_source(lines=33).replace('final guard', 'different guard')})
    packet, _ = query_repository(repo, state, 'validate', max_bytes=8192)
    verify_packet(repo, packet)
    assert {'a.py', 'b.py'} == {r['path'] for r in packet['selected']}
    assert all(r['coverage']['complete'] for r in packet['selected'])
    assert any('different guard' in r['source'] for r in packet['selected'])


@pytest.mark.parametrize('mutation', ['path', 'span', 'hash', 'coverage', 'duplicate'])
def test_alias_shape_and_literal_tampering_fail_closed(tmp_path, mutation):
    repo, state = indexed(tmp_path, {'a.py': 'def price(a):\n    return a\n',
                                    'b.py': 'def price(a):\n    return a\n'})
    packet, _ = query_repository(repo, state, 'price')
    alias = packet['selected'][0]['aliases'][0]
    if mutation == 'path': alias['path'] = '../outside.py'
    elif mutation == 'span': alias['span'] = [1, 0, 1, 4]
    elif mutation == 'hash': alias['file_sha256'] = '0' * 64
    elif mutation == 'coverage': alias['coverage']['complete'] = False
    else: packet['selected'][0]['aliases'].append(deepcopy(alias))
    with pytest.raises(LabError):
        verify_packet(repo, packet)


def test_existing_v1_packet_remains_verifiable(tmp_path):
    repo, state = indexed(tmp_path, {'a.py': 'def price(a):\n    return a\n'})
    packet, _ = query_repository(repo, state, 'price')
    packet['schema'] = 'codeintel-lab-packet-v1'
    packet.pop('scope')
    for row in packet['selected']:
        row.pop('aliases'); row.pop('coverage')
    verify_packet(repo, packet)


@pytest.mark.parametrize('bad', [None, 'bad', {'path': 'b.py'}, ['bad']])
def test_later_invalid_alias_shape_is_rejected_before_relationship_checks(tmp_path, monkeypatch, bad):
    repo, state = indexed(tmp_path, {'a.py': 'def price(a):\n    return a\n',
                                    'b.py': 'def price(a):\n    return a\n'})
    packet, _ = query_repository(repo, state, 'price')
    packet['selected'][0]['aliases'].append(bad)
    def forbidden(*args):
        pytest.fail('source read before full schema validation')
    monkeypatch.setattr('codeintel.lab.retrieval.source_bytes', forbidden)
    with pytest.raises(LabError, match='INVALID_PACKET'):
        verify_packet(repo, packet)


def test_long_shared_body_deduplicates_before_budgeting_second_full_copy(tmp_path):
    body = long_source(lines=65)
    repo, state = indexed(tmp_path, {'a.py': 'RATE = 1\n' + body,
                                    'b.py': 'RATE = 2\n' + body})
    packet, _ = query_repository(repo, state, 'validate', max_bytes=8192, limit=1)
    verify_packet(repo, packet)
    assert len(packet['selected']) == 1
    row = packet['selected'][0]
    assert row['coverage']['complete']
    assert [alias['path'] for alias in row['aliases']] == ['b.py']
    assert row['aliases'][0]['coverage']['complete']
