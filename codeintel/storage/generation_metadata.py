"""Bounded, unambiguous JSON for persisted generation metadata.

Count expanded occurrences rather than unique Python containers: JSON serializes
shared objects repeatedly. Admission precedes encoding; streaming encoding also
bounds structural/escaping overhead. No caller-owned nested container escapes.
"""
from __future__ import annotations

import json
import math
from typing import Any

from codeintel.storage.policy import DerivedStateValidationError

MAX_GENERATION_METADATA_BYTES = 64 * 1024
MAX_GENERATION_METADATA_DEPTH = 64


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate metadata key: {key!r}")
        result[key] = value
    return result


def _reject_constant(token: str):
    raise ValueError(f"non-finite JSON constant is not allowed: {token}")


def _admit_structure(metadata: dict, *, max_bytes: int) -> None:
    stack = [(metadata, 0)]
    observed = 0
    text_bytes = 0

    def admit_text(text: str) -> None:
        nonlocal text_bytes
        remaining = max_bytes - text_bytes
        if len(text) > remaining:
            raise ValueError("metadata exceeds UTF-8 byte bound")
        text_bytes += len(text.encode("utf-8"))
        if text_bytes > max_bytes:
            raise ValueError("metadata exceeds UTF-8 byte bound")

    while stack:
        current, depth = stack.pop()
        observed += 1
        if observed > max_bytes:
            raise ValueError("metadata exceeds expanded JSON node bound")
        if isinstance(current, (dict, list)):
            if current and depth >= MAX_GENERATION_METADATA_DEPTH:
                raise ValueError("metadata exceeds JSON depth bound or contains a cycle")
            # Every pending/child value requires at least one serialized byte.
            # Check before building a potentially enormous pending work list.
            if observed + len(stack) + len(current) > max_bytes:
                raise ValueError("metadata exceeds expanded JSON node bound")
            if isinstance(current, dict):
                for key in current:
                    if not isinstance(key, str):
                        raise ValueError("metadata object keys must be strings")
                    admit_text(key)
                children = current.values()
            else:
                children = current
            stack.extend((child, depth + 1) for child in children)
        elif isinstance(current, str):
            admit_text(current)
        elif current is None or isinstance(current, (bool, int)):
            if isinstance(current, int) and current.bit_length() > max_bytes * 4:
                raise ValueError("metadata integer exceeds serialized byte bound")
        elif isinstance(current, float) and math.isfinite(current):
            pass
        else:
            raise ValueError("metadata contains a non-JSON or non-finite value")


def encode_generation_metadata(metadata: dict, *, max_bytes: int = MAX_GENERATION_METADATA_BYTES) -> str:
    if not isinstance(metadata, dict):
        raise ValueError("metadata must be a JSON object")
    _admit_structure(metadata, max_bytes=max_bytes)
    encoder = json.JSONEncoder(sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    parts = []
    used = 0
    for part in encoder.iterencode(metadata):
        remaining = max_bytes - used
        if len(part) > remaining:
            raise ValueError(f"metadata must be <= {max_bytes} UTF-8 bytes")
        used += len(part.encode("utf-8"))
        if used > max_bytes:
            raise ValueError(f"metadata must be <= {max_bytes} UTF-8 bytes")
        parts.append(part)
    return "".join(parts)


def validate_generation_metadata(metadata: Any, label: str, *, max_bytes: int = MAX_GENERATION_METADATA_BYTES) -> dict:
    if metadata is None:
        return {}
    try:
        encoded = encode_generation_metadata(metadata, max_bytes=max_bytes)
        return json.loads(encoded)
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError(f"{label} must be strict JSON: {exc}") from exc


def load_generation_metadata(
    raw: Any, *, max_bytes: int = MAX_GENERATION_METADATA_BYTES,
    build_fingerprint: str | None = None,
) -> dict:
    try:
        if raw is None:
            value = {}
        else:
            if not isinstance(raw, str):
                raise ValueError("stored metadata must be text")
            if len(raw) > max_bytes or len(raw.encode("utf-8")) > max_bytes:
                raise ValueError(f"stored metadata must be <= {max_bytes} UTF-8 bytes")
            value = (
                json.loads(raw, parse_constant=_reject_constant, object_pairs_hook=_unique_object)
                if raw else {}
            )
            if not isinstance(value, dict):
                raise ValueError("stored metadata must be a JSON object")
        if build_fingerprint is not None:
            if not isinstance(build_fingerprint, str) or not build_fingerprint:
                raise ValueError("stored build fingerprint must be non-empty text")
            metadata_fingerprint = value.get("build_fingerprint")
            if metadata_fingerprint is None:
                value["build_fingerprint"] = build_fingerprint
            elif metadata_fingerprint != build_fingerprint:
                raise ValueError("stored build fingerprint conflicts with metadata")
        return validate_generation_metadata(value, "stored generation metadata", max_bytes=max_bytes)
    except (TypeError, ValueError, RecursionError) as exc:
        raise DerivedStateValidationError(f"stored generation metadata is not strict JSON: {exc}") from exc
