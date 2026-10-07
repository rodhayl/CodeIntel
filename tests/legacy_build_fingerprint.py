"""Exact prior-format fingerprints used only to construct upgrade fixtures."""
import hashlib
from typing import Optional
from codeintel.core.contracts import INDEX_CHUNK_POLICY_VERSION, INDEX_SNAPSHOT_FORMAT_VERSION, MAX_INDEX_CHUNK_BYTES
from codeintel.freshness.barrier import _installed_version


def legacy_build_fingerprint(*, schema_version="2.0.0-generation-local-fts",
                             chunking_policy_version=INDEX_CHUNK_POLICY_VERSION,
                             snapshot_format_version: Optional[str] = INDEX_SNAPSHOT_FORMAT_VERSION):
    runtime = {name: _installed_version(name, declared) for name, declared in (
        ("tree-sitter", "0.26.0"), ("tree-sitter-python", "0.25.0"),
        ("tree-sitter-javascript", "0.25.0"), ("tree-sitter-typescript", "0.23.2"),
    )}
    spec = (
        f"schema:{schema_version}|parser:treesitter-2026.09-js-grammar-v2|"
        f"ts_bind_decl:0.26.0|ts_bind_runtime:{runtime['tree-sitter']}|"
        f"py_gram_decl:0.25.0|py_gram_runtime:{runtime['tree-sitter-python']}|"
        f"js_gram_decl:0.25.0|js_gram_runtime:{runtime['tree-sitter-javascript']}|"
        f"ts_gram_decl:0.23.2|ts_gram_runtime:{runtime['tree-sitter-typescript']}|"
        f"chunk_policy:{chunking_policy_version}|chunk_bytes:{MAX_INDEX_CHUNK_BYTES}|"
        "scip_req:False|scip_py_avail:False|scip_py_ver:0.6.6|"
        "scip_py_fp:disabled|scip_ts_avail:False|scip_ts_ver:0.4.0|"
        "scip_ts_fp:disabled|scip_node_fp:disabled|"
        "dense_req:False|model:none|rev:none|dim:0|pre:v3-offline-only|embedding_fp:none"
    )
    if snapshot_format_version is not None:
        spec += f"|snapshot_format:{snapshot_format_version}"
    return hashlib.sha256(spec.encode("utf-8")).hexdigest()[:24]
