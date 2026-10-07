"""A stored source path is not an opaque file ID merely because of its prefix."""
import pytest

from codeintel.lab.retrieval import index_repository, query_repository, verify_packet
from codeintel.retrieval.hybrid import HybridRetriever


@pytest.mark.parametrize('relative', ['file_utils.py', 'file_helpers/a.py', 'src/file_utils.py', 'helpers.py'])
def test_ranked_exact_symbol_keeps_file_prefixed_paths(tmp_path, relative):
    repo, state = tmp_path / 'source', tmp_path / 'state'
    path = repo / relative
    path.parent.mkdir(parents=True)
    path.write_text('def special_helper():\n    return "review_unique_marker"\n')
    index_repository(repo, state)
    ranked, _ = query_repository(repo, state, 'special_helper')
    literal, _ = query_repository(repo, state, 'special_helper', baseline=True)
    assert ranked['status'] == literal['status'] == 'OK'
    assert ranked['selected'][0]['path'] == relative
    assert 'def special_helper' in ranked['selected'][0]['source']
    verify_packet(repo, ranked)


def test_an_opaque_id_cannot_replace_a_missing_source_path():
    retriever = HybridRetriever(object(), object())
    assert retriever._canonical_evidence_path(None, 'file_opaque', 'generation') is None
    assert retriever._canonical_evidence_path('file_opaque', 'file_opaque', 'generation') is None


def test_opaque_hint_uses_generation_bound_file_record_path():
    class Graph:
        def get_file_path(self, file_id, generation_id=None):
            assert (file_id, generation_id) == ('file_opaque', 'generation')
            return 'file_helpers/a.py'

    retriever = HybridRetriever(Graph(), object())
    assert retriever._canonical_evidence_path('file_opaque', 'file_opaque', 'generation') == 'file_helpers/a.py'


def test_valid_path_does_not_consult_redundant_backend_lookup():
    class Graph:
        calls = 0
        def get_file_path(self, *_args, **_kwargs):
            self.calls += 1
            raise AssertionError('valid stored path already resolves provenance')

    graph = Graph()
    retriever = HybridRetriever(graph, object())
    assert retriever._canonical_evidence_path('src/module.py', 'file_opaque', 'generation') == 'src/module.py'
    assert graph.calls == 0


def test_required_path_lookup_propagates_unexpected_backend_failure():
    class Graph:
        def get_file_path(self, *_args, **_kwargs):
            raise RuntimeError('backend failure')

    retriever = HybridRetriever(Graph(), object())
    with pytest.raises(RuntimeError, match='backend failure'):
        retriever._canonical_evidence_path(None, 'file_opaque', 'generation')
