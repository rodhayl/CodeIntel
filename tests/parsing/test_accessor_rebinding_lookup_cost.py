"""Property rebinding uses keyed lookup without changing normalized evidence."""
from dataclasses import asdict, replace
import hashlib
import json

from codeintel.parsing.python_accessors import normalize_python_property_accessors
from codeintel.parsing.treesitter_parser import TreeSitterCodeParser


def _raw(n=400):
    source = "".join(f"def function_{i}(): return {i}\n" for i in range(n)) + """class Client:
    @property
    def value(self): return 1
    @value.setter
    def value(self, v): self._value = v
"""
    entities, relations = TreeSitterCodeParser()._parse_python(
        "repo", "file", "gen", "sample.py", source
    )
    return source, entities, relations


def test_keyed_rebinding_preserves_complete_prechange_output():
    source, entities, relations = _raw()
    entities, relations = normalize_python_property_accessors(
        entities=entities, relations=relations, content=source
    )
    payload = json.dumps({"entities": [asdict(e) for e in entities],
                          "relations": [asdict(r) for r in relations]},
                         sort_keys=True, separators=(",", ":"))
    # Frozen from the same valid fixture before changing the lookup algorithm.
    assert hashlib.sha256(payload.encode()).hexdigest() == "3d85f10cb1d14eba197a754eb33ca7f01a57d99561c945e76c27ca2970f2f552"


def test_rebinding_does_not_compare_every_entity_with_every_other_entity():
    class CountedName(str):
        comparisons = 0
        __hash__ = str.__hash__

        def __eq__(self, other):
            type(self).comparisons += 1
            return super().__eq__(other)

    source, entities, relations = _raw()
    entities = [replace(entity, qualified_name=CountedName(entity.qualified_name))
                for entity in entities]
    CountedName.comparisons = 0
    normalized, _ = normalize_python_property_accessors(
        entities=entities, relations=relations, content=source
    )
    comparisons = CountedName.comparisons
    assert len(normalized) == len(entities)
    assert comparisons < len(entities) * 20
