import pytest
from codeintel.parsing.treesitter_parser import TreeSitterCodeParser
from codeintel.core.models import EntityKind, RelationType, TrustClass

def test_treesitter_python_parsing():
    parser = TreeSitterCodeParser()
    py_code = """
class Calculator:
    '''Docstring for Calculator.'''
    def add(self, a: int, b: int) -> int:
        return a + b

def run_calc():
    c = Calculator()
    return c.add(1, 2)
"""
    entities, relations, chunks = parser.parse_file("repo_1", "f_py", "gen_1", "calc.py", py_code)

    names = [e.name for e in entities]
    assert "Calculator" in names
    assert "add" in names
    assert "run_calc" in names

    cls_ent = [e for e in entities if e.name == "Calculator"][0]
    assert cls_ent.kind == EntityKind.CLASS
    assert cls_ent.docstring == "Docstring for Calculator."

    method_ent = [e for e in entities if e.name == "add"][0]
    assert method_ent.kind == EntityKind.METHOD

    defines_rels = [r for r in relations if r.rel_type == RelationType.DEFINES]
    assert len(defines_rels) >= 2

def test_treesitter_typescript_parsing():
    parser = TreeSitterCodeParser()
    ts_code = """
export interface User {
    id: string;
    name: string;
}

export class UserService {
    getUser(id: string): User {
        return { id, name: "Alice" };
    }
}
"""
    entities, relations, chunks = parser.parse_file("repo_1", "f_ts", "gen_1", "user.ts", ts_code)
    names = [e.name for e in entities]
    assert "User" in names
    assert "UserService" in names
    assert "getUser" in names
