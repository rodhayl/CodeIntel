"""The expansion prose describes conservative admission, not exact packing."""
from copy import deepcopy
from pathlib import Path

import pytest

from codeintel.lab.retrieval import index_repository, query_repository, serialize, verify_packet


@pytest.mark.parametrize('name', ['docs/portfolio/DESIGN.md'])
def test_expansion_prose_states_conservative_reserve(name):
    text = (Path(__file__).resolve().parents[1] / name).read_text()
    assert '64' in text and ('reserve' in text or 'reserva' in text)
    assert 'partial' in text or 'parcial' in text


def test_symbol_can_stay_partial_even_when_final_full_json_would_fit(tmp_path):
    repo, state = tmp_path / 'source', tmp_path / 'state'
    repo.mkdir()
    source = ('def validate(value):\n'
              + ''.join(f'    value += {i}  # ordinary work before final check\n' for i in range(65))
              + '    if value < 0:\n        raise ValueError("final guard")\n    return value\n')
    (repo / 'rules.py').write_text(source)
    index_repository(repo, state)
    full, _ = query_repository(repo, state, 'validate', max_bytes=8192)
    assert full['selected'][0]['coverage']['complete']
    budget = len(serialize(full))
    reference = deepcopy(full)
    reference['limits']['packet_bytes'] = budget
    assert len(serialize(reference)) <= budget
    partial, _ = query_repository(repo, state, 'validate', max_bytes=budget)
    verify_packet(repo, partial)
    assert len(serialize(partial)) <= budget
    assert not any(row['coverage']['complete'] for row in partial['selected'])
