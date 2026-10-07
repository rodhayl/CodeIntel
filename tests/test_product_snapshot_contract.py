"""New documentation cannot silently join the history-free product snapshot."""
import importlib.util
from pathlib import Path
import re

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('product_snapshot', ROOT / 'scripts/build_portfolio_snapshot.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)
REQUIRED = set(builder.FILES) | set(builder.PRODUCT_DOCS) | set(builder.PUBLIC_SCRIPTS)


@pytest.mark.parametrize('name', [
    'docs/portfolio/CV.md',
    'docs/portfolio/history/acceptance.json',
    'docs/portfolio/assets/editorial/unreviewed.svg',
    'docs/portfolio/articles/unreviewed.md',
    'docs/portfolio/new-technical-note.md',
    'docs/reports/private-report.json',
    'scripts/export_editorial.py',
])
def test_snapshot_rejects_unreviewed_document_admission(name):
    assert name not in builder.select_files(REQUIRED | {name})


def test_snapshot_keeps_runtime_tests_and_reproduction_inputs():
    runtime = {'codeintel/lab/retrieval.py', 'tests/test_portfolio_lab.py'}
    selected = builder.select_files(REQUIRED | runtime)
    assert runtime <= selected
    assert {
        'docs/portfolio/DEMO.txt', 'docs/portfolio/EVALUATION.txt',
        'docs/portfolio/runtime-inventory.json',
        'docs/portfolio/diagnostics/context-handoff-rubric.json',
        'docs/portfolio/diagnostics/adversarial-known-cases.json',
        'docs/portfolio/HISTORICAL_EVIDENCE.json',
        'docs/reports/portfolio-readiness/public/historical-measurements.json',
        'LICENSE', 'THIRD_PARTY_NOTICES.md',
    } <= selected
    assert 'docs/portfolio/' not in builder.PUBLIC_PREFIXES


def test_snapshot_missing_required_input_fails_closed():
    with pytest.raises(ValueError, match='Missing required snapshot inputs: docs/portfolio/DEMO.txt'):
        builder.select_files(REQUIRED - {'docs/portfolio/DEMO.txt'})


def test_product_docs_are_exact_reviewed_subset_and_links_are_closed():
    # No Git dependency: this test also runs from the extracted review archive.
    actual = {p.relative_to(ROOT).as_posix() for p in (ROOT / 'docs').rglob('*') if p.is_file()}
    assert actual == {name for name in REQUIRED if name.startswith('docs/')}
    selected = REQUIRED | {p.relative_to(ROOT).as_posix()
                           for prefix in builder.PUBLIC_PREFIXES
                           for p in (ROOT / prefix).rglob('*') if p.is_file()}
    for name in sorted(selected):
        if not name.endswith('.md'):
            continue
        path = ROOT / name
        for target in re.findall(r'!?\[[^\]]*\]\(([^)]+)\)', path.read_text()):
            if '://' in target or target.startswith('#'):
                continue
            resolved = (path.parent / target.split('#')[0]).resolve()
            assert resolved.is_relative_to(ROOT), (name, target)
            if resolved.is_dir():
                assert any((ROOT / item).is_relative_to(resolved) for item in selected), (name, target)
            else:
                assert resolved.relative_to(ROOT).as_posix() in selected, (name, target)
                assert resolved.is_file(), (name, target)
