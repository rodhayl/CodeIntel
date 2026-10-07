"""Authored source-gate identity for install-helper control-flow fixtures."""
import hashlib
import json


def write_source_receipt(path, files=None):
    ids = [['tests.test_authored_fixture', 'test_fixture']]
    digest = hashlib.sha256(json.dumps(ids, ensure_ascii=True, separators=(',', ':')).encode()).hexdigest()
    nodes = ['tests/test_authored_fixture.py::test_fixture']
    node_digest = hashlib.sha256(json.dumps(nodes, ensure_ascii=True, separators=(',', ':')).encode()).hexdigest()
    path.write_text(json.dumps({'schema': 'portfolio-consolidation-v3', 'status': 'PASS',
        'tested_source_commit': 'a' * 40, 'clean_before': True, 'clean_after': True, 'exit_code': 0,
        'junit': {'tests': 1, 'failures': 0, 'errors': 0, 'skipped': 0},
        'case_ids': ids, 'case_ids_sha256': digest,
        'collection': {'schema': 'codeintel-pytest-collection-v1', 'node_ids': nodes,
                       'node_ids_sha256': node_digest, 'case_ids': ids, 'case_ids_sha256': digest},
        'execution_collection': {'schema': 'codeintel-pytest-collection-v1', 'node_ids': nodes,
                                 'node_ids_sha256': node_digest, 'case_ids': ids, 'case_ids_sha256': digest},
        'source_file_sha256': files or {}}))
    return path
