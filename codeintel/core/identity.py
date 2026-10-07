import hashlib
import os
import re
from pathlib import Path, PurePosixPath
from typing import Tuple

_DRIVE_RE = re.compile(r"^[A-Za-z]:")


def _require_identity_text(value: str, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text")
    if not value or "\x00" in value:
        raise ValueError(f"{name} must be non-empty NUL-free text")
    return value


def validate_source_span(span: Tuple[int, int, int, int]) -> Tuple[int, int, int, int]:
    """Return one canonical 1-based-line, byte-column source span or fail closed."""
    if not isinstance(span, (tuple, list)) or len(span) != 4:
        raise ValueError("span must contain exactly four integer coordinates")
    if any(not isinstance(value, int) or isinstance(value, bool) for value in span):
        raise ValueError("span coordinates must be integers (non-negative integers)")
    start_line, start_col, end_line, end_col = span
    if start_line < 1 or end_line < 1 or start_col < 0 or end_col < 0:
        raise ValueError("span coordinates must be non-negative integers outside the canonical source domain")
    if end_line < start_line or (end_line == start_line and end_col < start_col):
        raise ValueError("span end must not precede start (end precedes start)")
    return (start_line, start_col, end_line, end_col)


def _span_preimage(span: Tuple[int, int, int, int]) -> str:
    start_line, start_col, end_line, end_col = validate_source_span(span)
    return f"{start_line}:{start_col}-{end_line}:{end_col}"


def canonical_hash(data: str) -> str:
    """Computes a deterministic SHA-256 hex digest for input data."""
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def normalize_repo_rel_path(rel_path: str) -> str:
    """Return a canonical repository-relative POSIX path or fail closed.

    Identity inputs must never depend on host-specific absolute paths or accept
    traversal components. Normal repository paths retain their historic ID preimage
    because backslashes normalize to '/' and redundant '.' components disappear.
    """
    if not isinstance(rel_path, str) or not rel_path or "\x00" in rel_path:
        raise ValueError("Repository relative path must be a non-empty text path")
    raw = rel_path.replace("\\", "/")
    if raw.startswith("/") or raw.startswith("//") or _DRIVE_RE.match(raw):
        raise ValueError(f"Repository path must be relative: {rel_path!r}")
    parts = []
    for part in PurePosixPath(raw).parts:
        if part in ("", "."):
            continue
        if part == "..":
            raise ValueError(f"Repository path traversal is not allowed: {rel_path!r}")
        parts.append(part)
    if not parts:
        raise ValueError("Repository relative path cannot resolve to repository root")
    normalized = "/".join(parts)
    if os.path.isabs(normalized) or _DRIVE_RE.match(normalized):
        raise ValueError(f"Repository path must be relative: {rel_path!r}")
    return normalized


def make_repo_id(canonical_path: str) -> str:
    """Deterministic repo_id based on one resolved physical repository path."""
    if not isinstance(canonical_path, str) or not canonical_path.strip() or "\x00" in canonical_path:
        raise ValueError("Repository path must be a non-empty text path")
    resolved = str(Path(canonical_path).expanduser().resolve(strict=False))
    return f"repo_{canonical_hash(resolved)[:16]}"


def make_file_id(repo_id: str, rel_path: str) -> str:
    """Deterministic file_id based on repo_id and validated relative path."""
    repo_id = _require_identity_text(repo_id, "repo_id")
    normalized_path = normalize_repo_rel_path(rel_path)
    return f"file_{canonical_hash(f'{repo_id}:{normalized_path}')[:16]}"


def make_entity_id(file_id: str, qualified_name: str, kind: str) -> str:
    """Deterministic entity_id independent of consumer models or tokenizers."""
    file_id = _require_identity_text(file_id, "file_id")
    qualified_name = _require_identity_text(qualified_name, "qualified_name")
    kind = _require_identity_text(kind, "kind")
    return f"ent_{canonical_hash(f'{file_id}:{qualified_name}:{kind}')[:16]}"


def make_relation_id(source_id: str, rel_type: str, target_id: str, file_id: str, span: Tuple[int, int, int, int]) -> str:
    """Deterministic relation_id for typed adjacency edges."""
    source_id = _require_identity_text(source_id, "source_id")
    rel_type = _require_identity_text(rel_type, "rel_type")
    target_id = _require_identity_text(target_id, "target_id")
    file_id = _require_identity_text(file_id, "file_id")
    span_str = _span_preimage(span)
    return f"rel_{canonical_hash(f'{source_id}:{rel_type}:{target_id}:{file_id}:{span_str}')[:16]}"


def make_chunk_id(file_id: str, generation_id: str, span: Tuple[int, int, int, int], content_hash: str) -> str:
    """Deterministic chunk_id."""
    file_id = _require_identity_text(file_id, "file_id")
    generation_id = _require_identity_text(generation_id, "generation_id")
    content_hash = _require_identity_text(content_hash, "content_hash")
    span_str = _span_preimage(span)
    return f"chk_{canonical_hash(f'{file_id}:{generation_id}:{span_str}:{content_hash}')[:16]}"




def make_evidence_id(generation_id: str, file_id: str, span: Tuple[int, int, int, int], content_hash: str) -> str:
    """Deterministic evidence_id based purely on canonical provenance."""
    generation_id = _require_identity_text(generation_id, "generation_id")
    file_id = _require_identity_text(file_id, "file_id")
    content_hash = _require_identity_text(content_hash, "content_hash")
    span_str = _span_preimage(span)
    return f"evi_{canonical_hash(f'{generation_id}:{file_id}:{span_str}:{content_hash}')[:16]}"
