"""Inventory local imports conservatively; execute no project code.

Import closure is supporting evidence, not proof a function is useful or correct.
Feature retirement and reproduced contracts are documented separately.
"""
from __future__ import annotations
import argparse
import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def inventory(root: Path) -> dict:
    files = sorted(p for p in (root / 'codeintel').rglob('*.py') if '__pycache__' not in p.parts)
    modules = {p.relative_to(root).as_posix()[:-3].replace('/', '.').removesuffix('.__init__'): p for p in files}
    graph = {}
    for module, path in modules.items():
        dependencies = set()
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                prefix = node.module or ''
                if node.level:
                    package = module if path.name == '__init__.py' else module.rsplit('.', 1)[0]
                    prefix = '.'.join(package.split('.')[:len(package.split('.')) - node.level + 1]) + ('.' + prefix if prefix else '')
                names = [prefix] + [prefix + '.' + a.name for a in node.names]
            else:
                continue
            dependencies.update(name for name in names if name in modules)
        dependencies.update('.'.join(module.split('.')[:i]) for i in range(1, len(module.split('.')))
                            if '.'.join(module.split('.')[:i]) in modules)
        graph[module] = sorted(dependencies)
    closure, todo = set(), ['codeintel.lab.entry', 'codeintel.lab.cli']
    while todo:
        module = todo.pop()
        if module not in closure:
            closure.add(module)
            todo.extend(graph.get(module, []))
    fixtures = {p for p in files if p.parent.name == 'fixture'}
    retained = {modules[m] for m in closure if m in modules} | fixtures
    return {'schema': 'codeintel-runtime-import-inventory-v1', 'python_files': len(files),
            'entrypoints': ['codeintel.lab.entry', 'codeintel.lab.cli'],
            'closure': sorted(closure),
            'retained_python_files': sorted(p.relative_to(root).as_posix() for p in retained),
            'outside_closure': sorted(p.relative_to(root).as_posix() for p in files if p not in retained),
            'method': 'Conservative AST imports, including function-local imports and package initializers; synthetic fixtures explicitly retained.',
            'limits': 'Does not prove every branch is necessary, reachable or correct. Pair with feature-level review and reproduced claims.'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, default=ROOT)
    args = p.parse_args()
    print(json.dumps(inventory(args.root.resolve()), indent=2))


if __name__ == '__main__':
    main()
