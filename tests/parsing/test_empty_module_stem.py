"""Eligible dotted filenames must not collapse to an empty module identity."""
import pytest

from codeintel.core.identity import make_entity_id
from codeintel.core.models import EntityKind
from codeintel.lab.retrieval import index_repository, query_repository, verify_packet
from codeintel.parsing.parser_dispatch import CanonicalTreeSitterCodeParser
from codeintel.parsing.treesitter_parser import strip_code_suffix
from codeintel.service.production import create_default_service


@pytest.mark.parametrize("name", ["..py", "...py", "..js", "...js", "..ts", "...ts"])
def test_dotted_stem_indexes_and_returns_its_exact_path(tmp_path, name):
    repo, state = tmp_path / "repo", tmp_path / "state"
    repo.mkdir()
    source = "def target(): return 1\n" if name.endswith(".py") else "function target() { return 1; }\n"
    (repo / name).write_text(source)
    indexed, _ = index_repository(repo, state)
    assert indexed["files"] == 1
    packet, _ = query_repository(repo, state, "target")
    assert packet["selected"][0]["path"] == name
    verify_packet(repo, packet)


def test_distinct_empty_stems_keep_distinct_entities_and_both_source_locations(tmp_path):
    repo, state = tmp_path / "repo", tmp_path / "state"
    repo.mkdir()
    for name in ("..py", "...py"):
        (repo / name).write_text("def target(): return 1\n")
    index_repository(repo, state)
    with create_default_service(str(repo), state_dir=str(state)) as service:
        generation = service.barrier.active_generation.generation_id
        entities = service.graph_store.get_entities_for_generation(generation)
        assert len({entity.entity_id for entity in entities}) == len(entities)
        modules = [entity for entity in entities if entity.kind == EntityKind.MODULE]
        assert {entity.qualified_name for entity in modules} == {"..py", "...py"}
    packet, _ = query_repository(repo, state, "target")
    paths = {location["path"] for row in packet["selected"]
             for location in [row, *row["aliases"]]}
    assert paths == {"..py", "...py"}
    verify_packet(repo, packet)


def test_normal_module_names_and_entity_ids_remain_unchanged():
    assert strip_code_suffix("pkg/normal.py") == "pkg.normal"
    assert strip_code_suffix("pkg/__init__.py") == "pkg.__init__"
    assert strip_code_suffix(".hidden.py") == "hidden"
    entities, _, _ = CanonicalTreeSitterCodeParser().parse_file(
        "repo", "file", "gen", "pkg/normal.py", "def target(): return 1\n"
    )
    by_kind = {entity.kind: entity for entity in entities}
    assert by_kind[EntityKind.MODULE].entity_id == make_entity_id("file", "pkg.normal", "MODULE")
    assert by_kind[EntityKind.FUNCTION].entity_id == make_entity_id("file", "pkg.normal.target", "FUNCTION")
