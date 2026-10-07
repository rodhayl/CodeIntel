import pytest

from codeintel.core.models import Entity, EntityKind
from codeintel.parsing.chunk_partition import partition_entity_chunks


def _entity(*, entity_id, qname, kind, span, parent_id=None):
    return Entity(
        entity_id=entity_id,
        repo_id="repo",
        file_id="file_example",
        generation_id="gen_1",
        name=qname.rsplit(".", 1)[-1],
        qualified_name=qname,
        kind=kind,
        span=span,
        properties={} if parent_id is None else {"parent_id": parent_id},
    )


def test_conflicting_entity_id_remains_fail_closed_with_structural_diagnostic():
    module = _entity(
        entity_id="ent_module",
        qname="pkg.example",
        kind=EntityKind.MODULE,
        span=(1, 0, 5, 0),
    )
    first = _entity(
        entity_id="ent_collision",
        qname="pkg.example.request",
        kind=EntityKind.FUNCTION,
        span=(1, 0, 2, 0),
        parent_id="ent_module",
    )
    second = _entity(
        entity_id="ent_collision",
        qname="pkg.example.request",
        kind=EntityKind.FUNCTION,
        span=(3, 0, 4, 0),
        parent_id="ent_module",
    )

    with pytest.raises(ValueError) as caught:
        partition_entity_chunks(
            entities=[module, first, second],
            content="def request():\n    pass\n\ndef request(x):\n    pass\n",
            rel_path="pkg/example.py",
            file_id="file_example",
            generation_id="gen_1",
        )

    message = str(caught.value)
    assert "conflicting duplicate entity_id ent_collision" in message
    assert "pkg/example.py" in message
    assert "qname='pkg.example.request'" in message
    assert "kind='FUNCTION'" in message
    assert "span=(1, 0, 2, 0)" in message
    assert "span=(3, 0, 4, 0)" in message
    assert "parent_id='ent_module'" in message
    assert "def request" not in message
