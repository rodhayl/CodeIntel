from dataclasses import replace

from codeintel.core.identity import make_entity_id
from codeintel.core.models import EntityKind, RelationType
from codeintel.parsing.parser_dispatch import CanonicalTreeSitterCodeParser
from codeintel.parsing.python_accessors import normalize_python_property_accessors


def _parse(content: str):
    parser = CanonicalTreeSitterCodeParser()
    return parser.parse_file(
        repo_id="repo_test",
        file_id="file_property",
        generation_id="gen_1",
        rel_path="pkg/example.py",
        content=content,
    )


def _property_source(getter_body: str, setter_body: str) -> str:
    return f'''class Client:\n    @property\n    def timeout(self) -> int:\n        {getter_body}\n\n    @timeout.setter\n    def timeout(self, value: int) -> None:\n        {setter_body}\n'''


def test_property_getter_and_setter_share_qname_but_have_distinct_stable_ids():
    entities, relations, chunks = _parse(
        _property_source("return read_timeout()", "write_timeout(value)")
    )
    accessors = [
        entity
        for entity in entities
        if entity.qualified_name == "pkg.example.Client.timeout"
    ]
    assert len(accessors) == 2
    by_role = {entity.properties.get("python_accessor_role"): entity for entity in accessors}
    assert set(by_role) == {"getter", "setter"}

    getter = by_role["getter"]
    setter = by_role["setter"]
    assert getter.entity_id != setter.entity_id
    assert getter.entity_id == make_entity_id(
        "file_property", "pkg.example.Client.timeout", EntityKind.METHOD.value
    )
    assert setter.entity_id == make_entity_id(
        "file_property",
        "pkg.example.Client.timeout#python-property-setter",
        EntityKind.METHOD.value,
    )
    assert getter.properties["python_accessor_variant"] == "property-getter"
    assert setter.properties["python_accessor_variant"] == "property-setter"

    defines = [relation for relation in relations if relation.rel_type == RelationType.DEFINES]
    defined_accessor_ids = {
        relation.target_id for relation in defines if relation.target_id in {getter.entity_id, setter.entity_id}
    }
    assert defined_accessor_ids == {getter.entity_id, setter.entity_id}

    calls = [relation for relation in relations if relation.rel_type == RelationType.CALLS]
    call_sources = {relation.source_id for relation in calls}
    assert getter.entity_id in call_sources
    assert setter.entity_id in call_sources
    assert chunks
    assert any(getter.entity_id in chunk.entity_ids for chunk in chunks)
    assert any(setter.entity_id in chunk.entity_ids for chunk in chunks)


def test_property_accessor_ids_survive_body_only_edits():
    first_entities, _relations, _chunks = _parse(
        _property_source("return read_timeout()", "write_timeout(value)")
    )
    second_entities, _relations, _chunks = _parse(
        _property_source("return read_timeout() + 1", "write_timeout(value + 1)")
    )

    def ids(entities):
        return {
            entity.properties.get("python_accessor_role"): entity.entity_id
            for entity in entities
            if entity.qualified_name == "pkg.example.Client.timeout"
        }

    assert ids(first_entities) == ids(second_entities)


def test_repeated_python_methods_keep_distinct_source_ids():
    source = '''class Client:\n    def request(self) -> int:\n        return 1\n\n    def request(self) -> int:\n        return 2\n'''
    entities, relations, chunks = _parse(source)
    methods = [entity for entity in entities if entity.qualified_name == "pkg.example.Client.request"]
    assert len(methods) == 2
    assert len({entity.entity_id for entity in methods}) == 2
    assert {relation.target_id for relation in relations if relation.rel_type == RelationType.DEFINES} >= {
        entity.entity_id for entity in methods
    }
    assert all(any(entity.entity_id in chunk.entity_ids for chunk in chunks) for entity in methods)


def test_overlapping_repeated_method_observations_remain_ambiguous():
    source = '''class Client:\n    def request(self):\n        return 1\n\n    def request(self):\n        return 2\n'''
    entities, _relations, _chunks = _parse(source)
    first, second = sorted(
        (entity for entity in entities if entity.qualified_name == "pkg.example.Client.request"),
        key=lambda entity: entity.span,
    )
    overlap = replace(second, entity_id=first.entity_id, span=(first.span[0] + 1, 0, second.span[2], second.span[3]))
    normalized, _ = normalize_python_property_accessors(
        entities=[first, overlap], relations=[], content=source
    )
    assert [entity.entity_id for entity in normalized] == [first.entity_id, first.entity_id]


def test_repeated_local_callbacks_keep_distinct_source_and_relation_identity():
    source = '''def render(flag):
    if flag:
        def callback():
            first()
    else:
        def callback():
            second()
'''
    entities, relations, chunks = _parse(source)
    callbacks = sorted(
        (entity for entity in entities if entity.qualified_name == "pkg.example.render.callback"),
        key=lambda entity: entity.span,
    )
    assert len(callbacks) == 2
    assert callbacks[0].entity_id == make_entity_id(
        "file_property", "pkg.example.render.callback", EntityKind.FUNCTION.value
    )
    assert callbacks[1].entity_id == make_entity_id(
        "file_property", "pkg.example.render.callback#python-definition-2", EntityKind.FUNCTION.value
    )
    callback_ids = {entity.entity_id for entity in callbacks}
    assert {relation.target_id for relation in relations if relation.rel_type == RelationType.DEFINES} >= callback_ids
    assert {relation.source_id for relation in relations if relation.rel_type == RelationType.CALLS} >= callback_ids
    assert all(any(entity.entity_id in chunk.entity_ids for chunk in chunks) for entity in callbacks)


def test_repeated_top_level_classes_keep_distinct_source_ids():
    source = '''class Client:\n    pass\n\nclass Client:\n    pass\n'''
    entities, relations, chunks = _parse(source)
    classes = [entity for entity in entities if entity.qualified_name == "pkg.example.Client"]
    assert len(classes) == 2
    assert len({entity.entity_id for entity in classes}) == 2
    assert {relation.target_id for relation in relations if relation.rel_type == RelationType.DEFINES} >= {
        entity.entity_id for entity in classes
    }
    assert all(any(entity.entity_id in chunk.entity_ids for chunk in chunks) for entity in classes)
