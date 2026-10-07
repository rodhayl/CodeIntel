"""Validate historical SCIP diagnostics in persisted generation metadata."""

from __future__ import annotations

_LANGUAGES = frozenset({"python", "typescript", "javascript", "snapshot"})
_STATUSES = frozenset({
    "EXEC_ERROR", "INTERNAL_ERROR", "MISSING_OUTPUT", "NONZERO_EXIT", "OUTPUT_LIMIT",
    "SNAPSHOT_LIMIT", "TIMEOUT", "TOOLCHAIN_CHANGED", "TOOL_UNAVAILABLE",
    "UNKNOWN", "UNSAFE_OUTPUT",
})


def validate_scip_failure_codes(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 4:
        raise ValueError("SCIP failure codes must be a bounded list")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or ":" not in item:
            raise ValueError("SCIP failure code is malformed")
        language, status = item.split(":", 1)
        if language not in _LANGUAGES or status not in _STATUSES:
            raise ValueError("SCIP failure code is unknown")
        if item in result:
            raise ValueError("SCIP failure code is duplicated")
        result.append(item)
    return result
