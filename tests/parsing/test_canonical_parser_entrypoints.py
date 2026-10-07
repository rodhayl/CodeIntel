"""Both retained parser imports preserve the lab's canonical source policy."""
import pytest

from codeintel.parsing import TreeSitterCodeParser
from codeintel.parsing.chunking import bound_chunks
from codeintel.parsing.parser_dispatch import CanonicalTreeSitterCodeParser


@pytest.mark.parametrize("parser_type", [TreeSitterCodeParser, CanonicalTreeSitterCodeParser], ids=["package", "dispatcher"])
@pytest.mark.parametrize("path,source", [
    ("a.py", 'prefix = "x\u2028y"\nclass Box:\n    @property\n    def size(self):\n        return 1\n    @size.setter\n    def size(self, value):\n        pass\ntail = 2\n'),
    ("a.js", 'const prefix = `x\ry`;\nclass Box { get size() { return 1; } set size(v) {} }\nconst tail = 2;'),
    ("a.jsx", 'const element = <div>ok</div>;\nfunction render() { return element; }'),
    ("a.ts", 'function f(v: string): string;\nfunction f(v: number): number;\nfunction f(v: any) { return v; }'),
    ("a.tsx", 'const element = <div>ok</div>;\nfunction render(): unknown { return element; }'),
    ("notes.txt", 'α\u2028β\rγ\nlast'),
    ("empty.py", ''),
])
def test_entrypoint_preserves_literal_source_and_unique_identities(parser_type, path, source):
    entities, _relations, chunks = parser_type().parse_file("repo", "file", "generation", path, source)
    assert len({entity.entity_id for entity in entities}) == len(entities)
    raw = source.encode("utf-8")
    starts = [0] + [i + 1 for i, value in enumerate(raw) if value == 10]
    covered = set()
    for chunk in bound_chunks(chunks):
        sl, sc, el, ec = chunk.span
        begin, end = starts[sl - 1] + sc, starts[el - 1] + ec
        assert raw[begin:end] == chunk.content.encode("utf-8")
        assert not covered.intersection(range(begin, end))
        covered.update(range(begin, end))
    assert all(index in covered or bytes([value]).isspace() for index, value in enumerate(raw))
