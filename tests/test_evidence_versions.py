"""The current inventory must bind every retained runtime source byte."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORTFOLIO = ROOT / 'docs/portfolio'


def test_current_inventory_covers_exact_runtime_bytes():
    import importlib.util
    # Maintenance scripts are deliberately not installed as runtime packages.
    # Load this read-only AST helper by its delivered file, without inserting
    # the source checkout on sys.path or masking the wheel under test.
    spec = importlib.util.spec_from_file_location('reviewed_scope_inventory', ROOT / 'scripts/audit_runtime_scope.py')
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    current = json.loads((PORTFOLIO / 'runtime-inventory.json').read_text())
    observed = helper.inventory(ROOT)
    assert observed['outside_closure'] == []
    assert current['python_files'] == observed['python_files']
    assert current['retained_python_files'] == observed['retained_python_files']
    assert current['runtime_sha256'] == {
        name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        for name in observed['retained_python_files']
    }


def test_current_inventory_covers_non_python_fixtures_too():
    current = json.loads((PORTFOLIO / 'runtime-inventory.json').read_text())
    actual = {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in (ROOT / 'codeintel').rglob('*')
              if path.is_file() and path.suffix in ('.py', '.js', '.ts')}
    assert current['runtime_files_sha256'] == actual
    assert current['runtime_files'] == len(actual)
    assert {name for name in actual if not name.endswith('.py')} == {
        'codeintel/lab/fixture/billing.ts', 'codeintel/lab/fixture/display.js'}
