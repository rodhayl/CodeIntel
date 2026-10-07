"""A small observable adapter over the existing generation-bound service.

The byte limit applies to the complete serialized query packet, including its
newline. Telemetry is separate and never stores query text or source text.
"""
from __future__ import annotations

import hashlib
import heapq
import json
import math
import os
import re
import stat
import time
from pathlib import Path

from codeintel import __version__
from codeintel.core.contracts import MAX_INDEX_SOURCE_FILE_BYTES
from codeintel.core.identity import normalize_repo_rel_path, validate_source_span
from codeintel.freshness.production import _open_canonical_repo_file
from codeintel.freshness.errors import (
    SourceInputError, SourceValidationError, SourceLimitError, SourceEnumerationError,
    SourcePathPolicyError,
)
from codeintel.service import create_default_service

SCHEMA = "codeintel-lab-packet-v2"
LEGACY_SCHEMA = "codeintel-lab-packet-v1"
SCOPE = ("Selected literals only; symbol coverage excludes dependencies. Aliases share text, "
         "not imports/constants. Candidates capped at 32; aliases are not exhaustive. "
         "For partial symbols read the recorded range or full file.")
CANDIDATE_LIMIT = 32
MIN_PACKET_BYTES = 1024
MAX_PACKET_BYTES = 8192
SELECTIONS = {
    "ranked": "lexical/exact-symbol ranking with heuristic syntax neighbors; whole fragments",
    "baseline": "literal substring; alphabetical path/span order; whole fragments",
}
# Older packets retain the same literal verification contract. Their historical
# descriptor is accepted for verification, but is never emitted for new queries.
_LEGACY_RANKED_SELECTION = "existing lexical/exact-symbol/verified syntax graph ranking; whole fragments"
_SHA256 = re.compile(r"[0-9a-f]{64}")


class LabError(ValueError):
    def __init__(self, code: str, recovery: str):
        super().__init__(code)
        self.code, self.recovery = code, recovery


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def serialize(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                       allow_nan=False) + "\n").encode("utf-8")


def _require_utf8_path(path: Path, *, code: str, recovery: str) -> None:
    try:
        str(path).encode("utf-8")
    except UnicodeError as exc:
        raise LabError(code, recovery) from exc


def open_service(repo: Path, state: Path):
    if not repo.is_dir():
        raise LabError("REPOSITORY_MISSING", "Choose an existing source directory.")
    resolved_repo = repo.resolve()
    _require_utf8_path(resolved_repo, code="SOURCE_UNAVAILABLE",
                       recovery="Use a repository path with valid UTF-8 spelling, then index or query again.")
    # APSW accepts UTF-8 filenames. Validate the effective path before the
    # service creates/chmods a state directory or writes its layout marker.
    # Expansion/resolution here is read-only: pass the original path below so
    # validate_trusted_state_dir still observes every lexical symlink ancestor.
    recovery = "Choose a state directory with a valid UTF-8 path outside the repository."
    try:
        expanded_state = state.expanduser()
        _require_utf8_path(expanded_state.resolve(), code="INVALID_STATE_PATH", recovery=recovery)
    except RuntimeError as exc:
        raise LabError("INVALID_STATE_PATH", "Choose a resolvable, non-symlink state path outside the repository.") from exc
    # Deliberately ignore provider environment and local discovery for this lab.
    # Preserve symlinks for validate_trusted_state_dir; it canonicalizes only
    # after checking the requested final component and every ancestor.
    try:
        return create_default_service(str(resolved_repo), state_dir=str(state))
    except SourceInputError as exc:
        raise source_input_error(exc) from exc


def source_input_error(error: SourceInputError) -> LabError:
    if isinstance(error, SourcePathPolicyError):
        return LabError("SOURCE_UNAVAILABLE",
                        "Rename source paths containing a literal backslash to canonical names, then index or query again.")
    if isinstance(error, SourceValidationError):
        return LabError("SOURCE_INVALID_UTF8",
                        "Convert supported source files to valid UTF-8 text, then index or query again.")
    if isinstance(error, SourceLimitError):
        return LabError("SOURCE_TOO_LARGE", "Use files within the index file-size limit.")
    if isinstance(error, SourceEnumerationError):
        return LabError("SOURCE_ENUMERATION_FAILED",
                        "Check trusted Git availability and repository access; keep candidate counts within the index limits.")
    return LabError("SOURCE_UNAVAILABLE",
                    "Restore regular, readable source and ignore files without unsafe symlinks, then retry.")


def source_bytes(repo: Path, path: str) -> bytes:
    path = normalize_repo_rel_path(path)
    try:
        with _open_canonical_repo_file(str(repo.resolve()), path) as stream:
            # The shared opener uses O_NONBLOCK on POSIX. Inspect the opened
            # descriptor before reading so a replacement FIFO/device cannot be
            # accepted merely because it supplies the expected source bytes.
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise LabError("SOURCE_UNAVAILABLE", "Restore a regular source file, then index and query again.")
            if info.st_size > MAX_INDEX_SOURCE_FILE_BYTES:
                raise LabError("SOURCE_TOO_LARGE", "Use files within the index file-size limit.")
            raw = stream.read(MAX_INDEX_SOURCE_FILE_BYTES + 1)
    except SourceInputError as exc:
        raise source_input_error(exc) from exc
    except OSError as exc:
        raise LabError("SOURCE_UNAVAILABLE", "Restore the file, then index and query again.") from exc
    if len(raw) > MAX_INDEX_SOURCE_FILE_BYTES:
        raise LabError("SOURCE_TOO_LARGE", "Use files within the index file-size limit.")
    return raw


def literal_span(raw: bytes, span) -> bytes:
    start, left, end, right = validate_source_span(span)
    # split only on LF: tree-sitter rows and columns refer to UTF-8 bytes.
    lines = raw.split(b"\n")
    if end > len(lines) or left > len(lines[start - 1]) or right > len(lines[end - 1]):
        raise LabError("INVALID_SPAN", "Index and query again; do not edit packet provenance.")
    if start == end:
        return lines[start - 1][left:right]
    return b"\n".join([lines[start - 1][left:], *lines[start:end - 1], lines[end - 1][:right]])


def _validate_packet_schema(packet: dict) -> None:
    """Validate the entire JSON shape before reading any selected source."""
    legacy = isinstance(packet, dict) and packet.get("schema") == LEGACY_SCHEMA
    keys = {"schema", "version", "status", "snapshot_sha256", "refreshed", "limits", "selection", "selected", "omitted"}
    if not legacy:
        keys.add("scope")
    limit_keys = {"packet_bytes", "fragments", "candidates", "unit"}
    omission_keys = {"duplicate", "budget", "limit"}
    row_keys = {"path", "span", "source", "source_sha256", "file_sha256", "reason", "score"}

    if not legacy:
        row_keys |= {"aliases", "coverage"}

    def require(condition):
        if not condition:
            raise LabError("INVALID_PACKET", "Use a packet produced by codeintel lab query.")

    def valid_digest(value):
        return isinstance(value, str) and _SHA256.fullmatch(value) is not None

    require(isinstance(packet, dict) and set(packet) == keys)
    require(packet["schema"] in {SCHEMA, LEGACY_SCHEMA} and packet["version"] == __version__)
    if not legacy:
        require(packet["scope"] == SCOPE)
    require(isinstance(packet["status"], str) and packet["status"] in {"OK", "EMPTY", "BUDGET_EXHAUSTED"})
    require(valid_digest(packet["snapshot_sha256"]) and type(packet["refreshed"]) is bool)
    require(isinstance(packet["selection"], str) and (
        packet["selection"] in SELECTIONS.values() or packet["selection"] == _LEGACY_RANKED_SELECTION
    ))
    limits, omitted, selected = packet["limits"], packet["omitted"], packet["selected"]
    require(isinstance(limits, dict) and set(limits) == limit_keys)
    budget, count = limits["packet_bytes"], limits["fragments"]
    require(type(budget) is int and MIN_PACKET_BYTES <= budget <= MAX_PACKET_BYTES)
    require(type(count) is int and 1 <= count <= 20)
    require(type(limits["candidates"]) is int and limits["candidates"] == CANDIDATE_LIMIT)
    require(limits["unit"] == "UTF-8 bytes")
    require(isinstance(omitted, dict) and set(omitted) == omission_keys)
    require(all(type(value) is int and 0 <= value <= CANDIDATE_LIMIT for value in omitted.values()))
    require(isinstance(selected, list) and len(selected) <= count)
    require(len(selected) + sum(omitted.values()) <= CANDIDATE_LIMIT)
    if packet["status"] == "OK":
        require(bool(selected))
    elif packet["status"] == "EMPTY":
        require(not selected and not any(omitted.values()))
    else:
        require(not selected and omitted["budget"] > 0 and omitted["duplicate"] == omitted["limit"] == 0)
    hashes = set()
    locations = set()
    file_hashes = {}
    aliases = 0
    for row in selected:
        require(isinstance(row, dict) and set(row) == row_keys)
        require(isinstance(row["path"], str) and normalize_repo_rel_path(row["path"]) == row["path"])
        require(isinstance(row["span"], list))
        validate_source_span(row["span"])
        require(isinstance(row["source"], str))
        require(isinstance(row["reason"], str) and bool(row["reason"]))
        require(type(row["score"]) in (int, float))
        require(type(row["score"]) is int or math.isfinite(row["score"]))
        require(valid_digest(row["source_sha256"]) and valid_digest(row["file_sha256"]))
        require(file_hashes.setdefault(row["path"], row["file_sha256"]) == row["file_sha256"])
        require(row["source_sha256"] not in hashes)
        hashes.add(row["source_sha256"])
        if not legacy:
            require(isinstance(row["aliases"], list))
            aliases += len(row["aliases"])
            for location in [row, *row["aliases"]]:
                if location is not row:
                    require(isinstance(location, dict) and set(location) == {"path", "span", "file_sha256", "coverage"})
                    require(isinstance(location["path"], str) and normalize_repo_rel_path(location["path"]) == location["path"])
                    require(isinstance(location["span"], list))
                    validate_source_span(location["span"])
                    require(valid_digest(location["file_sha256"]))
                    require(file_hashes.setdefault(location["path"], location["file_sha256"]) == location["file_sha256"])
                identity = (location["path"], tuple(location["span"]))
                require(identity not in locations)
                locations.add(identity)
                coverage = location["coverage"]
                require(isinstance(coverage, dict) and set(coverage) == {"symbol_span", "complete"})
                symbol = coverage["symbol_span"]
                require(type(coverage["complete"]) is bool)
                if symbol is None:
                    require(coverage["complete"] is False)
                else:
                    require(isinstance(symbol, list))
                    validate_source_span(symbol)
    if not legacy:
        require(aliases == omitted["duplicate"])
        # Only inspect relationships after *all* nested shapes are validated.
        for row in selected:
            for location in [row, *row["aliases"]]:
                require(_covers_symbol(packet, location) == location["coverage"]["complete"])

    require(len(serialize(packet)) <= budget)


def _covers_symbol(packet: dict, location: dict) -> bool:
    """Only literal byte-range coverage, never dependencies or parser authenticity."""
    symbol = location["coverage"]["symbol_span"]
    if symbol is None:
        return False
    cursor, end = tuple(symbol[:2]), tuple(symbol[2:])
    spans = sorted(tuple(item["span"]) for row in packet["selected"]
                   for item in [row, *row.get("aliases", [])]
                   if item["path"] == location["path"])
    for span in spans:
        left, right = tuple(span[:2]), tuple(span[2:])
        if right <= cursor:
            continue
        if left > cursor:
            return False
        cursor = max(cursor, right)
        if cursor >= end:
            return True
    return False


def verify_packet(repo: Path, packet: dict) -> None:
    """Verify literals from one observed read per file, not an atomic repo snapshot."""
    try:
        _validate_packet_schema(packet)
        by_path = {}
        for row in packet["selected"]:
            for location in [row, *row.get("aliases", [])]:
                by_path.setdefault(location["path"], []).append((row, location))
        # Keep only one bounded file buffer at a time. The schema already
        # requires a single file hash for every occurrence of each path.
        for path, selected_locations in by_path.items():
            raw = source_bytes(repo, path)
            if digest(raw) != selected_locations[0][1]["file_sha256"]:
                raise LabError("STALE_SOURCE", "Run query again to refresh the index and packet.")
            for row, location in selected_locations:
                literal = literal_span(raw, location["span"])
                if digest(literal) != row["source_sha256"] or literal.decode("utf-8") != row["source"]:
                    raise LabError("PROVENANCE_MISMATCH", "Discard the packet; index and query again.")
                symbol = location.get("coverage", {}).get("symbol_span")
                if symbol is not None:
                    literal_span(raw, symbol)  # reject even partial out-of-file scope

    except LabError:
        raise
    except (KeyError, TypeError, UnicodeError, ValueError) as exc:
        raise LabError("INVALID_PACKET", "Use a packet produced by codeintel lab query.") from exc


def index_repository(repo: Path, state: Path) -> tuple[dict, dict]:
    started = time.perf_counter()
    service = open_service(repo, state)
    try:
        result = service.reindex()
        if result["status"] not in {"SUCCESS", "REUSED_EXISTING"}:
            raise LabError("INDEX_FAILED", "Inspect source validity and retry in a new state directory.")
        packet = {"schema": "codeintel-lab-index-v1", "version": __version__,
                  "status": result["status"], "files": result["files_count"],
                  "snapshot_sha256": service.barrier.active_generation.snapshot_hash,
                  "dense": "off", "scip": False}
        return packet, {"operation": "index", **packet,
                        "duration_seconds": time.perf_counter() - started}
    except SourceInputError as exc:
        raise source_input_error(exc) from exc
    finally:
        service.close()


def query_repository(repo: Path, state: Path, query: str, *, max_bytes: int = 4096,
                     limit: int = 5, baseline: bool = False) -> tuple[dict, dict]:
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or not MIN_PACKET_BYTES <= max_bytes <= MAX_PACKET_BYTES:
        raise LabError("INVALID_BUDGET", "Use --max-bytes between 1024 and 8192 (UTF-8 bytes).")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 20:
        raise LabError("INVALID_LIMIT", "Use --limit between 1 and 20.")
    query_recovery = "Use a nonempty query of at most 1024 valid UTF-8 bytes."
    if not isinstance(query, str) or not query.strip() or len(query) > 1024:
        raise LabError("INVALID_QUERY", query_recovery)
    try:
        query_bytes = query.encode("utf-8")
    except UnicodeError as exc:
        # POSIX argv may contain surrogate-escaped bytes. Reject them before
        # opening state, rather than leaking a traceback or the query text.
        raise LabError("INVALID_QUERY", query_recovery) from exc
    if len(query_bytes) > 1024:
        raise LabError("INVALID_QUERY", query_recovery)
    started = time.perf_counter()
    service = open_service(repo, state)
    try:
        before = service.barrier.active_generation
        if before is None:
            raise LabError("INDEX_REQUIRED", "Run codeintel lab index with the same --repo and --state-dir first.")
        if baseline:
            service.ensure_fresh()
            generation_id = service.barrier.active_generation.generation_id
            # Scan the same complete indexed corpus, retaining only the best
            # candidate prefix. This bounds Python materialization, not total
            # scan work, and preserves the original alphabetical selection.
            folded_query = query.casefold()
            chunks = heapq.nsmallest(
                CANDIDATE_LIMIT,
                (c for c in service.lexical_index._iter_retrieval_chunks(generation_id)
                 if folded_query in c.content.casefold()),
                key=lambda c: (c.rel_path, c.span, c.content_hash))
            candidates = [{"generation_id": generation_id, "path": c.rel_path,
                           "span": c.span, "source_text": c.content,
                           "content_hash": c.content_hash, "score": 1.0,
                           "reason_selected": "literal substring; alphabetical path/span order"}
                          for c in chunks]
        else:
            candidates = service.search(query, limit=CANDIDATE_LIMIT)
            candidates.sort(key=lambda c: (-c["score"], c["path"], c["span"], c["content_hash"]))
        generation = service.barrier.active_generation
        packet = {"schema": SCHEMA, "version": __version__, "status": "EMPTY",
                  "snapshot_sha256": generation.snapshot_hash,
                  "refreshed": before.generation_id != generation.generation_id,
                  "limits": {"packet_bytes": max_bytes, "fragments": limit,
                             "candidates": CANDIDATE_LIMIT, "unit": "UTF-8 bytes"},
                  "selection": SELECTIONS["baseline" if baseline else "ranked"],
                  "selected": [], "omitted": {"duplicate": 0, "budget": 0, "limit": 0}, "scope": SCOPE}
        seen: dict[str, dict] = {}
        omitted = []
        files = {}
        expanded = set()
        for candidate in candidates:
            if candidate["generation_id"] != generation.generation_id:
                raise LabError("GENERATION_MISMATCH", "Retry after repository writes stop.")
            path = normalize_repo_rel_path(candidate["path"])
            if path not in files:
                files[path] = source_bytes(repo, path)
            raw = files[path]
            text = candidate["source_text"]
            literal = literal_span(raw, candidate["span"])
            if literal != text.encode("utf-8") or digest(literal) != candidate["content_hash"]:
                raise LabError("PROVENANCE_MISMATCH", "Stop repository writes, then index and query again.")
            symbol = candidate.get("symbol_span")
            symbol_key = (path, tuple(symbol)) if symbol else None
            if symbol_key in expanded:
                continue  # absorbed into the expanded full-symbol candidate
            span = candidate["span"]
            row = {"path": path, "span": list(span), "source": text,
                   "source_sha256": digest(literal), "file_sha256": digest(raw),
                   "reason": candidate["reason_selected"], "score": candidate["score"],
                   "aliases": [], "coverage": {"symbol_span": list(symbol) if symbol else None, "complete": False}}
            if symbol:
                full = literal_span(raw, symbol)
                full_row = {**row, "span": list(symbol), "source": full.decode("utf-8"),
                            "source_sha256": digest(full)}
                # A repeated full symbol costs only an alias. Do not reject
                # expansion by budgeting a second body that will never be sent.
                owner = seen.get(full_row["source_sha256"])
                if owner is not None:
                    owner["aliases"].append({key: full_row[key] for key in
                                             ("path", "span", "file_sha256", "coverage")})
                    fits = len(serialize(packet)) + 64 <= max_bytes
                    owner["aliases"].pop()
                else:
                    trial = {**packet, "selected": [*packet["selected"], full_row], "status": "OK"}
                    fits = len(serialize(trial)) + 64 <= max_bytes
                if fits:
                    row = full_row
                    expanded.add(symbol_key)
            source_hash = row["source_sha256"]
            reason = None
            if source_hash in seen:
                owner = seen[source_hash]
                alias = {key: row[key] for key in ("path", "span", "file_sha256", "coverage")}
                owner["aliases"].append(alias)
                if len(serialize(packet)) + 64 > max_bytes:
                    owner["aliases"].pop()
                    reason = "budget"
                else:
                    reason = "duplicate"
            elif len(packet["selected"]) >= limit:
                reason = "limit"
            else:
                packet["selected"].append(row)
                packet["status"] = "OK"
                # Reserve enough space for final omission counts/status.
                if len(serialize(packet)) + 64 > max_bytes:
                    packet["selected"].pop()
                    reason = "budget"
                else:
                    seen[source_hash] = row
            if reason:
                packet["omitted"][reason] += 1
                omitted.append({"path": path, "span": row["span"],
                                "source_sha256": source_hash, "reason": reason})
        packet["status"] = "OK" if packet["selected"] else ("BUDGET_EXHAUSTED" if candidates else "EMPTY")
        for row in packet["selected"]:
            for location in [row, *row["aliases"]]:
                location["coverage"]["complete"] = _covers_symbol(packet, location)
        verify_packet(repo, packet)
        fresh, *_ = service.barrier.check_freshness()
        if not fresh or service.barrier.active_generation.generation_id != generation.generation_id:
            raise LabError("SOURCE_CHANGED", "Stop repository writes and retry; nothing was emitted.")
        telemetry = {"schema": "codeintel-lab-telemetry-v1", "version": __version__,
                     "operation": "query", "query_sha256": digest(query_bytes),
                     "config": {"dense": "off", "scip": False,
                                "retrieval": "literal_scan" if baseline else "ranked", **packet["limits"]},
                     "snapshot_sha256": generation.snapshot_hash, "status": packet["status"],
                     "duration_seconds": time.perf_counter() - started,
                     "packet_bytes": len(serialize(packet)), "packet_sha256": digest(serialize(packet)),
                     "source_bytes": sum(len(r["source"].encode("utf-8")) for r in packet["selected"]),
                     "duplicate_content_bytes": 0, "duplicate_metric": "exact full-fragment hashes within one packet",
                     "selected": [{k: v for k, v in row.items() if k != "source"} for row in packet["selected"]],
                     "omitted": omitted, "delivery": "prepared", "agent_received": None,
                     "agent_retained": None, "agent_reread": None, "tokens": None, "cost": None}
        return packet, telemetry
    except SourceInputError as exc:
        raise source_input_error(exc) from exc
    finally:
        service.close()
