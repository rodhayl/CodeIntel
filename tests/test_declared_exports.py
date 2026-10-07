"""Declared exports are consumers even when a binding has no AST Name use."""
import ast
import importlib
from pathlib import Path

import codeintel
import pytest


def _export_modules():
    root = Path(codeintel.__file__).parent
    modules = []
    for path in sorted(root.rglob('*.py')):
        tree = ast.parse(path.read_text())
        if any(isinstance(node, ast.Name) and node.id == '__all__'
               and isinstance(node.ctx, ast.Store) for node in ast.walk(tree)):
            name = '.'.join(path.relative_to(root).with_suffix('').parts)
            modules.append('codeintel.' + name.removesuffix('.__init__'))
    return modules


@pytest.mark.parametrize('module_name', _export_modules())
def test_every_declared_export_is_bound_and_star_importable(module_name):
    module = importlib.import_module(module_name)
    # fcntl's explicit list is Windows-only; POSIX intentionally forwards the
    # native module's public bindings. Test the live contract on this platform.
    exports = getattr(module, '__all__', [name for name in vars(module)
                                        if not name.startswith('_')])
    assert len(exports) == len(set(exports))
    namespace = {}
    exec(f'from {module_name} import *', namespace)
    for name in exports:
        assert isinstance(name, str)
        assert namespace[name] is getattr(module, name)


def test_chunk_policy_reexport_preserves_its_canonical_binding():
    from codeintel.core.contracts import INDEX_CHUNK_POLICY_VERSION
    from codeintel.parsing.chunking import INDEX_CHUNK_POLICY_VERSION as exported
    assert exported is INDEX_CHUNK_POLICY_VERSION
