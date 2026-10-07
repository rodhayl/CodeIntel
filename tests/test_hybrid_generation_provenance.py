from types import SimpleNamespace

import pytest

from codeintel.core.identity import canonical_hash
from codeintel.core.models import Chunk
from codeintel.retrieval.hybrid import HybridRetriever


def _chunk(generation_id: str, *, chunk_id: str = "chunk_1", file_id: str = "file_1") -> Chunk:
    content = "pass"
    return Chunk(
        chunk_id=chunk_id,
        file_id=file_id,
        generation_id=generation_id,
        rel_path="src/app.py",
        span=(1, 0, 1, 4),
        content=content,
        content_hash=canonical_hash(content),
        entity_ids=["ent_1"],
    )


class Lexical:
    def __init__(self, chunk):
        self.chunk = chunk

    def search(self, query, limit=20, generation_id=None):
        return [(self.chunk, 1.0)]

    def get_chunks_for_file(self, file_id, generation_id=None):
        return [self.chunk]

    def get_chunk(self, chunk_id, generation_id=None):
        return self.chunk


class Graph:
    def get_entities_by_name(self, name, generation_id=None):
        return []


def test_lexical_backend_cannot_relabel_stale_chunk_as_requested_generation():
    retriever = HybridRetriever(Graph(), Lexical(_chunk("gen_old")))

    evidence = retriever.retrieve(
        query="find implementation",
        generation_id="gen_current",
        limit=5,
        expand_graph=False,
        enable_exact_entity=False,
    )

    assert evidence == []


def test_literal_entity_lookup_requires_entity_and_chunk_generation_file_provenance():
    current_entity = SimpleNamespace(
        entity_id="ent_1",
        file_id="file_1",
        generation_id="gen_current",
        span=(1, 0, 1, 4),
    )
    retriever = HybridRetriever(Graph(), Lexical(_chunk("gen_old")))
    assert retriever._literal_chunk_for_entity(current_entity, "gen_current") is None

    stale_entity = SimpleNamespace(
        entity_id="ent_1",
        file_id="file_1",
        generation_id="gen_old",
        span=(1, 0, 1, 4),
    )
    retriever = HybridRetriever(Graph(), Lexical(_chunk("gen_current")))
    assert retriever._literal_chunk_for_entity(stale_entity, "gen_current") is None

    wrong_file = _chunk("gen_current", file_id="file_other")
    retriever = HybridRetriever(Graph(), Lexical(wrong_file))
    assert retriever._literal_chunk_for_entity(current_entity, "gen_current") is None


def test_evidence_from_chunk_never_overwrites_chunk_generation_provenance():
    retriever = HybridRetriever(Graph(), Lexical(_chunk("gen_old")))
    assert retriever._evidence_from_chunk(_chunk("gen_old"), "gen_current") is None

    current = _chunk("gen_current")
    evidence = retriever._evidence_from_chunk(current, "gen_current")
    assert evidence is not None
    assert evidence.generation_id == current.generation_id == "gen_current"
    assert evidence.file_id == current.file_id


def test_internal_neighbor_type_error_is_not_retried_as_legacy_interface():
    class FailingGraph(Graph):
        calls = 0

        def get_callers(self, entity_id, max_depth=1, generation_id=None, include_heuristic=False):
            self.calls += 1
            raise TypeError('internal backend defect')

    graph = FailingGraph()
    retriever = HybridRetriever(graph, Lexical(_chunk('gen_current')))
    with pytest.raises(TypeError, match='internal backend defect'):
        retriever.retrieve('find implementation', 'gen_current', enable_exact_entity=False)
    assert graph.calls == 1
