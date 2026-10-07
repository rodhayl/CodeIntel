"""Production SQLiteStore with explicit per-instance lifecycle policy."""

import math
import os
import re
import stat
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import apsw

from codeintel.core.contracts import MAX_TASK_QUERY_BYTES
from codeintel.core.identity import canonical_hash, make_chunk_id
from codeintel.core.models import Chunk, EntityKind
from codeintel.core.security import STATE_LAYOUT_VERSION
from codeintel.storage.generation_metadata import (
    MAX_GENERATION_METADATA_BYTES,
    encode_generation_metadata,
    load_generation_metadata,
    validate_generation_metadata,
)
from codeintel.storage.lifecycle import cleanup_orphan_vector_artifacts
from codeintel.storage.policy import (
    CODEINTEL_DB_BUSY_TIMEOUT_MS,
    DerivedStateValidationError,
    decode_stored_json,
    validate_stored_span,
    validate_production_rows,
    configure_codeintel_connection,
    fts_expression,
    fts_read_snapshot,
    validate_fts_coherence,
    validate_production_schema,
)
from codeintel.storage.sqlite_store import SQLiteStore as BaseSQLiteStore

MAX_PRODUCTION_FTS_TERMS = 64
MAX_PRODUCTION_FTS_LIMIT = 512
# Short-query bounds apply globally to candidate rows/content fetched into
# Python, not SQLite internal reads, cache allocation or physical database I/O.
MAX_SHORT_LITERAL_CHUNKS = 4096
MAX_SHORT_LITERAL_BYTES = 4 * 1024 * 1024
_SAFE_GENERATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SAFE_SHA256 = re.compile(r"^(?:[0-9a-f]{16}|[0-9a-f]{64})$")


class ProductionSQLiteStore(BaseSQLiteStore):
    """SQLiteStore whose CodeIntel policy and close lifecycle are first-class."""

    def __init__(self, db_path: str):
        db = Path(db_path)
        self._closed = False
        db_text = str(db)

        if os.path.lexists(db_text):
            st = os.lstat(db_text)
            if stat.S_ISLNK(st.st_mode):
                raise DerivedStateValidationError("CodeIntel db.sqlite must not be a symlink")
            if not stat.S_ISREG(st.st_mode):
                raise DerivedStateValidationError("CodeIntel db.sqlite must be a regular file")

        preflight = None
        if db.exists() and db.stat().st_size > 0:
            # Read the effective database including pending WAL, without owning
            # a writer connection whose final close could checkpoint rejected
            # state. An immutable URI would incorrectly ignore pending WAL.
            readonly = apsw.Connection(db_text, flags=apsw.SQLITE_OPEN_READONLY)
            try:
                readonly.set_busy_timeout(CODEINTEL_DB_BUSY_TIMEOUT_MS)
                validate_production_schema(readonly)
                validate_production_rows(readonly)
            except BaseException as readonly_error:
                try:
                    readonly.close()
                except BaseException as cleanup_error:
                    readonly_error.add_note(
                        "Production SQLite read-only preflight cleanup also failed: "
                        f"{type(cleanup_error).__name__}: {cleanup_error}"
                    )
                raise
            else:
                readonly.close()

            preflight = apsw.Connection(db_text)
            try:
                # Revalidate after opening RW: another process may have changed
                # the version after the read-only probe. Keep rejected DB/WAL
                # bytes intact on close, then restore normal close behavior once
                # this connection has passed the compatibility/policy gate.
                preflight.config(apsw.SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE, True)
                preflight.set_busy_timeout(CODEINTEL_DB_BUSY_TIMEOUT_MS)
                validate_production_schema(preflight)
                configure_codeintel_connection(preflight)
                preflight.config(apsw.SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE, False)
            except BaseException as preflight_error:
                try:
                    preflight.close()
                except BaseException as cleanup_error:
                    preflight_error.add_note(
                        "Production SQLite preflight cleanup also failed: "
                        f"{type(cleanup_error).__name__}: {cleanup_error}"
                    )
                raise

        marker = db.parent / ".codeintel_state_layout"
        if not marker.exists() and not db.exists():
            marker.write_text(STATE_LAYOUT_VERSION + "\n", encoding="utf-8")

        try:
            # Keep the connection validated before schema initialization.
            # A second connection would repeat full integrity and foreign-key
            # scans; post-initialization schema validation still runs below.
            if preflight is None:
                super().__init__(db_path)
            else:
                super().__init__(db_path, _connection=preflight)
            validate_production_schema(self._conn)
            if preflight is None:
                configure_codeintel_connection(self._conn)
                validate_production_schema(self._conn)

            def valid_generation_ids():
                # Cleanup consumes this iterator only after taking rebuild.lock.
                # An eager SELECT can miss a generation committed before that lock.
                for row in self._conn.execute("SELECT generation_id FROM generations"):
                    yield str(row["generation_id"] if isinstance(row, dict) else row[0])

            self.last_lifecycle_cleanup = cleanup_orphan_vector_artifacts(
                str(db.parent), valid_generation_ids()
            )
        except BaseException as init_error:
            conn = getattr(self, "_conn", None)
            if conn is None:
                conn = preflight
            if conn is not None:
                try:
                    conn.close()
                except BaseException as cleanup_error:
                    init_error.add_note(
                        "ProductionSQLiteStore initialization cleanup also failed: "
                        f"{type(cleanup_error).__name__}: {cleanup_error}"
                    )
            self._closed = True
            raise

    @staticmethod
    def _validated_metadata(metadata: Optional[Dict[str, Any]], label: str) -> Dict[str, Any]:
        return validate_generation_metadata(
            metadata, label, max_bytes=MAX_GENERATION_METADATA_BYTES
        )

    @staticmethod
    def _serialize_metadata(metadata: Dict[str, Any]) -> str:
        return encode_generation_metadata(metadata, max_bytes=MAX_GENERATION_METADATA_BYTES)

    @staticmethod
    def _load_metadata(raw: Optional[str], build_fingerprint=None) -> Dict[str, Any]:
        return load_generation_metadata(
            raw, max_bytes=MAX_GENERATION_METADATA_BYTES,
            build_fingerprint=build_fingerprint,
        )

    def create_generation(
        self,
        repo_id: str,
        sequence: int,
        snapshot_hash: str,
        build_fingerprint: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        if not isinstance(repo_id, str) or not repo_id:
            raise ValueError("repo_id must be a non-empty string")
        if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence <= 0:
            raise ValueError("generation sequence must be a positive integer")
        if not isinstance(snapshot_hash, str) or not _SAFE_SHA256.fullmatch(snapshot_hash):
            raise ValueError("snapshot_hash must be a lowercase SHA-256 hex digest")
        if build_fingerprint is not None and (
            not isinstance(build_fingerprint, str) or not build_fingerprint
        ):
            raise ValueError("build_fingerprint must be None or a non-empty string")
        clean_metadata = self._validated_metadata(metadata, "generation metadata")
        return super().create_generation(
            repo_id=repo_id,
            sequence=sequence,
            snapshot_hash=snapshot_hash,
            build_fingerprint=build_fingerprint,
            metadata=clean_metadata,
        )

    def update_generation_metadata(
        self, generation_id: str, metadata: Dict[str, Any]
    ) -> None:
        if not isinstance(generation_id, str) or not _SAFE_GENERATION_ID.fullmatch(generation_id):
            raise ValueError("generation_id contains unsafe metadata characters")
        update = self._validated_metadata(metadata, "generation metadata update")
        fingerprint = update.get("build_fingerprint")
        if fingerprint is not None and (not isinstance(fingerprint, str) or not fingerprint):
            raise ValueError("build_fingerprint must be None or a non-empty string")
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._execute(
                    "SELECT metadata_json, build_fingerprint FROM generations WHERE generation_id = ?",
                    (generation_id,),
                ).fetchone()
                if row is None:
                    raise ValueError(f"Generation {generation_id} does not exist.")
                existing = self._load_metadata(row["metadata_json"], row["build_fingerprint"])
                existing.update(update)
                existing = self._validated_metadata(existing, "merged generation metadata")
                serialized = self._serialize_metadata(existing)
                new_build_fp = existing.get("build_fingerprint")
                if new_build_fp is not None:
                    self._execute(
                        "UPDATE generations SET metadata_json = ?, build_fingerprint = ? WHERE generation_id = ?",
                        (serialized, new_build_fp, generation_id),
                    )
                else:
                    self._execute(
                        "UPDATE generations SET metadata_json = ? WHERE generation_id = ?",
                        (serialized, generation_id),
                    )
                self._conn.execute("COMMIT")
            except BaseException:
                if self._conn.in_transaction:
                    self._conn.execute("ROLLBACK")
                raise

    def _atomic_batch(self, name: str, operation: Callable[[], Any]) -> Any:
        """Make inherited multi-row writes all-or-nothing on the owned connection."""
        with self._lock:
            savepoint = f"codeintel_{name}_batch"
            self._conn.execute(f"SAVEPOINT {savepoint}")
            try:
                result = operation()
                self._conn.execute(f"RELEASE SAVEPOINT {savepoint}")
                return result
            except BaseException:
                if self._conn.in_transaction:
                    self._conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                    self._conn.execute(f"RELEASE SAVEPOINT {savepoint}")
                raise

    def _validate_batch_scope(self, items, label: str) -> Tuple[str, str]:
        """Require one existing generation and its owning repo for every batch row."""
        generations = set()
        repos = set()
        for item in items:
            generation_id = getattr(item, "generation_id", None)
            repo_id = getattr(item, "repo_id", None)
            if (
                not isinstance(generation_id, str)
                or not _SAFE_GENERATION_ID.fullmatch(generation_id)
            ):
                raise ValueError(f"{label} batch contains an invalid generation_id")
            if not isinstance(repo_id, str) or not repo_id:
                raise ValueError(f"{label} batch contains an invalid repo_id")
            generations.add(generation_id)
            repos.add(repo_id)
        if len(generations) != 1 or len(repos) != 1:
            raise ValueError(f"{label} batch must belong to exactly one generation and repo")
        generation_id = next(iter(generations))
        repo_id = next(iter(repos))
        with self._lock:
            row = self._execute(
                "SELECT repo_id FROM generations WHERE generation_id = ?",
                (generation_id,),
            ).fetchone()
        if row is None:
            raise ValueError(f"{label} batch generation does not exist: {generation_id}")
        if row["repo_id"] != repo_id:
            raise ValueError(
                f"{label} batch repo_id does not own generation {generation_id}"
            )
        return generation_id, repo_id

    def _missing_scoped_ids(
        self, generation_id: str, kind: str, values
    ) -> set[str]:
        schema = {
            "file": ("files", "file_id"),
            "entity": ("entities", "entity_id"),
        }
        try:
            table, column = schema[kind]
        except KeyError as exc:
            raise ValueError(f"unsupported scoped ID kind: {kind}") from exc
        wanted = {value for value in values if isinstance(value, str) and value}
        if not wanted:
            return set()
        found: set[str] = set()
        ordered = sorted(wanted)
        with self._lock:
            for start in range(0, len(ordered), 400):
                batch = ordered[start : start + 400]
                placeholders = ",".join("?" for _ in batch)
                rows = self._execute(
                    f"SELECT {column} FROM {table} "
                    f"WHERE generation_id = ? AND {column} IN ({placeholders})",
                    tuple([generation_id, *batch]),
                ).fetchall()
                found.update(str(row[column]) for row in rows)
        return wanted - found

    def _validate_entity_references(self, entities, generation_id: str) -> None:
        invalid = [
            getattr(entity, "file_id", None)
            for entity in entities
            if not isinstance(getattr(entity, "file_id", None), str)
            or not getattr(entity, "file_id", None)
        ]
        if invalid:
            raise ValueError("entities batch contains an invalid file_id")
        missing_files = self._missing_scoped_ids(
            generation_id, "file", [entity.file_id for entity in entities]
        )
        if missing_files:
            raise ValueError(
                "entities batch references files absent from its generation: "
                + ", ".join(sorted(missing_files)[:3])
            )

    def _validate_relation_references(self, relations, generation_id: str) -> None:
        for relation in relations:
            for attr in ("file_id", "source_id", "target_id"):
                value = getattr(relation, attr, None)
                if not isinstance(value, str) or not value:
                    raise ValueError(f"relations batch contains an invalid {attr}")
        missing_files = self._missing_scoped_ids(
            generation_id, "file", [relation.file_id for relation in relations]
        )
        if missing_files:
            raise ValueError(
                "relations batch references files absent from its generation: "
                + ", ".join(sorted(missing_files)[:3])
            )
        endpoint_ids = [
            entity_id
            for relation in relations
            for entity_id in (relation.source_id, relation.target_id)
        ]
        missing_entities = self._missing_scoped_ids(
            generation_id, "entity", endpoint_ids
        )
        if missing_entities:
            raise ValueError(
                "relations batch references entities absent from its generation: "
                + ", ".join(sorted(missing_entities)[:3])
            )

    @staticmethod
    def _validate_chunk_literal_identity(chunk: Chunk) -> None:
        """Bind persisted chunk provenance to its exact literal UTF-8 source."""
        if not isinstance(chunk.content, str):
            raise ValueError("chunk content must be text")
        span = chunk.span
        if not isinstance(span, tuple) or len(span) != 4:
            raise ValueError("chunk span must be a four-item tuple")
        if any(not isinstance(value, int) or isinstance(value, bool) for value in span):
            raise ValueError("chunk span coordinates must be integers")
        start_line, start_col, end_line, end_col = span
        if start_line < 1 or end_line < 1 or start_col < 0 or end_col < 0:
            raise ValueError("chunk span coordinates are out of range")
        if end_line < start_line or (end_line == start_line and end_col < start_col):
            raise ValueError("chunk span end precedes its start")

        raw = chunk.content.encode("utf-8")
        newline_count = raw.count(b"\n")
        expected_end_line = start_line + newline_count
        expected_end_col = (
            start_col + len(raw)
            if newline_count == 0
            else len(raw.rsplit(b"\n", 1)[1])
        )
        if (end_line, end_col) != (expected_end_line, expected_end_col):
            raise ValueError(
                "chunk span end does not match its literal UTF-8 content"
            )

        expected_hash = canonical_hash(chunk.content)
        if chunk.content_hash != expected_hash:
            raise ValueError("chunk content_hash does not match its literal content")
        expected_id = make_chunk_id(
            chunk.file_id,
            chunk.generation_id,
            chunk.span,
            expected_hash,
        )
        if chunk.chunk_id != expected_id:
            raise ValueError("chunk_id does not match canonical chunk provenance")

    def _validate_chunk_references(self, chunks: List[Chunk]) -> str:
        generations = set()
        chunk_ids = set()
        file_ids = set()
        all_entity_ids = set()
        for chunk in chunks:
            if not isinstance(chunk, Chunk):
                raise TypeError("production chunks batch must contain only Chunk objects")
            if (
                not isinstance(chunk.generation_id, str)
                or not _SAFE_GENERATION_ID.fullmatch(chunk.generation_id)
            ):
                raise ValueError("chunks batch contains an invalid generation_id")
            generations.add(chunk.generation_id)
            if not isinstance(chunk.chunk_id, str) or not chunk.chunk_id:
                raise ValueError("chunks batch contains an invalid chunk_id")
            if chunk.chunk_id in chunk_ids:
                raise ValueError("chunks batch contains duplicate chunk_ids")
            chunk_ids.add(chunk.chunk_id)
            if not isinstance(chunk.file_id, str) or not chunk.file_id:
                raise ValueError("chunks batch contains an invalid file_id")
            file_ids.add(chunk.file_id)
            if not isinstance(chunk.rel_path, str) or not chunk.rel_path:
                raise ValueError("chunks batch contains an invalid rel_path")
            if not isinstance(chunk.entity_ids, list):
                raise ValueError("chunk entity_ids must be a list")
            if any(not isinstance(entity_id, str) or not entity_id for entity_id in chunk.entity_ids):
                raise ValueError("chunk entity_ids must contain non-empty strings")
            if len(chunk.entity_ids) != len(set(chunk.entity_ids)):
                raise ValueError("chunk entity_ids must not contain duplicates")
            all_entity_ids.update(chunk.entity_ids)

        if len(generations) != 1:
            raise ValueError("chunks batch must belong to exactly one generation")
        generation_id = next(iter(generations))
        with self._lock:
            generation = self._execute(
                "SELECT generation_id FROM generations WHERE generation_id = ?",
                (generation_id,),
            ).fetchone()
            if generation is None:
                raise ValueError(f"chunks batch generation does not exist: {generation_id}")

            file_rows = self._execute(
                "SELECT file_id, rel_path FROM files WHERE generation_id = ?",
                (generation_id,),
            ).fetchall()
            file_paths = {str(row["file_id"]): str(row["rel_path"]) for row in file_rows}
            missing_files = file_ids - set(file_paths)
            if missing_files:
                raise ValueError(
                    "chunks batch references files absent from its generation: "
                    + ", ".join(sorted(missing_files)[:3])
                )
            for chunk in chunks:
                if file_paths[chunk.file_id] != chunk.rel_path:
                    raise ValueError(
                        f"chunk rel_path does not match file provenance: {chunk.chunk_id}"
                    )

            entity_owner: dict[str, str] = {}
            if all_entity_ids:
                ordered = sorted(all_entity_ids)
                for start in range(0, len(ordered), 400):
                    batch = ordered[start : start + 400]
                    placeholders = ",".join("?" for _ in batch)
                    rows = self._execute(
                        "SELECT entity_id, file_id FROM entities "
                        f"WHERE generation_id = ? AND entity_id IN ({placeholders})",
                        tuple([generation_id, *batch]),
                    ).fetchall()
                    entity_owner.update(
                        {str(row["entity_id"]): str(row["file_id"]) for row in rows}
                    )
                missing_entities = all_entity_ids - set(entity_owner)
                if missing_entities:
                    raise ValueError(
                        "chunks batch references entities absent from its generation: "
                        + ", ".join(sorted(missing_entities)[:3])
                    )
                for chunk in chunks:
                    wrong_file = [
                        entity_id
                        for entity_id in chunk.entity_ids
                        if entity_owner[entity_id] != chunk.file_id
                    ]
                    if wrong_file:
                        raise ValueError(
                            f"chunk references entities owned by another file: {chunk.chunk_id}"
                        )
        for chunk in chunks:
            self._validate_chunk_literal_identity(chunk)
        return generation_id

    def activate_generation(self, generation_id: str) -> None:
        if not isinstance(generation_id, str) or not _SAFE_GENERATION_ID.fullmatch(generation_id):
            raise ValueError("generation_id contains unsafe activation characters")
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("BEGIN IMMEDIATE")
            try:
                cursor.execute(
                    "SELECT repo_id, metadata_json, build_fingerprint FROM generations WHERE generation_id = ?",
                    (generation_id,),
                )
                row = cursor.fetchone()
                if not row:
                    raise ValueError(f"Generation {generation_id} does not exist.")
                metadata = self._load_metadata(row["metadata_json"], row["build_fingerprint"])
                if metadata.get("lifecycle_state") != "COMMITTED":
                    raise RuntimeError(
                        f"Production activation requires COMMITTED lifecycle state: {generation_id}"
                    )
                repo_id = row["repo_id"]
                cursor.execute(
                    "UPDATE generations SET is_active = 0 WHERE repo_id = ?",
                    (repo_id,),
                )
                # Pin validation to this activation's writer transaction. A bad
                # target must roll back the old generation's deactivation too.
                validate_fts_coherence(self._conn, generation_id)
                cursor.execute(
                    "UPDATE generations SET is_active = 1 WHERE generation_id = ?",
                    (generation_id,),
                )
                cursor.execute("COMMIT")
            except BaseException:
                if self._conn.in_transaction:
                    cursor.execute("ROLLBACK")
                raise

    def add_files(self, files):
        if not files:
            return None
        self._validate_batch_scope(files, "files")
        return self._atomic_batch("files", lambda: super(ProductionSQLiteStore, self).add_files(files))

    def add_entities(self, entities):
        if not entities:
            return None
        generation_id, _repo_id = self._validate_batch_scope(entities, "entities")
        self._validate_entity_references(entities, generation_id)
        return self._atomic_batch("entities", lambda: super(ProductionSQLiteStore, self).add_entities(entities))

    def add_relations(self, relations):
        if not relations:
            return None
        generation_id, _repo_id = self._validate_batch_scope(relations, "relations")
        self._validate_relation_references(relations, generation_id)
        return self._atomic_batch("relations", lambda: super(ProductionSQLiteStore, self).add_relations(relations))

    def index_chunks(self, chunks: List[Chunk], is_full_replacement: bool = True) -> None:
        if not chunks:
            return None
        if not isinstance(is_full_replacement, bool):
            raise ValueError("is_full_replacement must be boolean")
        self._validate_chunk_references(chunks)
        return super().index_chunks(chunks, is_full_replacement=is_full_replacement)



    def _row_to_entity(self, row):
        validate_stored_span(row)
        try:
            EntityKind(row["kind"])
        except ValueError as exc:
            raise DerivedStateValidationError("Stored entity kind is invalid; rebuild required") from exc
        decode_stored_json(row["properties_json"], label="entity properties_json", shape=dict)
        return super()._row_to_entity(row)

    @staticmethod
    def _row_to_chunk(row):
        validate_stored_span(row)
        decode_stored_json(row["entity_ids_json"], label="chunk entity_ids_json", shape=list)
        return BaseSQLiteStore._row_to_chunk(row)

    @staticmethod
    def _validate_fts_request(query: str, limit: int, generation_id: Optional[str]) -> str:
        if not isinstance(query, str):
            raise ValueError("lexical query must be a string")
        if len(query.encode("utf-8")) > MAX_TASK_QUERY_BYTES:
            raise ValueError(
                f"lexical query must be <= {MAX_TASK_QUERY_BYTES} UTF-8 bytes"
            )
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or limit < 1
            or limit > MAX_PRODUCTION_FTS_LIMIT
        ):
            raise ValueError(
                f"lexical limit must be an integer between 1 and {MAX_PRODUCTION_FTS_LIMIT}"
            )
        if generation_id is not None:
            if not isinstance(generation_id, str) or not _SAFE_GENERATION_ID.fullmatch(generation_id):
                raise ValueError("generation_id contains unsafe lexical-scope characters")
        return query.strip()

    @staticmethod
    def _fts_tokens(query: str) -> List[str]:
        primary = [token for token in re.findall(r"[\w/.\-]+", query, flags=re.UNICODE) if len(token) >= 3]
        tokens: List[str] = []
        seen = set()
        for token in primary:
            folded = token.casefold()
            if folded in seen:
                continue
            seen.add(folded)
            tokens.append(token)
            if len(tokens) >= MAX_PRODUCTION_FTS_TERMS:
                break
        return tokens

    @classmethod
    def _fts_expression(cls, query: str) -> Optional[str]:
        return fts_expression(cls._fts_tokens(query))

    def _search_short_literal_rows(self, query: str, limit: int, generation_id: Optional[str]):
        """Casefolded short-token OR over a bounded indexed-chunk prefix.

        Only called when no >=3-character FTS term exists. Metadata is visited
        in generation/chunk-ID order, using the existing primary-key index;
        content is fetched into Python only while both budgets permit it.
        SQLite may inspect additional bytes to compute candidate byte lengths;
        this does not bound physical database I/O or SQLite memory. Matching
        rows are ordered by path/span within each visited scope, then those
        scopes round-robin as in FTS. This is not an absence proof or an
        alphabetical prefix of the whole repository.
        """
        terms = list(dict.fromkeys(token.casefold() for token in
                     re.findall(r"[\w]+", query, flags=re.UNICODE)
                     if 1 <= len(token) <= 2))[:MAX_PRODUCTION_FTS_TERMS]
        if not terms:
            return []
        with self._lock, fts_read_snapshot(self._conn):
            if generation_id is None:
                scopes = self._execute(
                    "SELECT generation_id FROM generations WHERE is_active = 1 "
                    "ORDER BY repo_id, generation_id LIMIT ?",
                    (MAX_SHORT_LITERAL_CHUNKS,)).fetchall()
            else:
                scopes = self._execute("SELECT generation_id FROM generations WHERE generation_id = ?",
                                       (generation_id,)).fetchall()
            remaining_chunks = MAX_SHORT_LITERAL_CHUNKS
            remaining_bytes = MAX_SHORT_LITERAL_BYTES
            groups = []
            for scope in scopes:
                if not remaining_chunks or not remaining_bytes:
                    break
                current = scope["generation_id"]
                candidates = self._execute(
                    "SELECT chunk_id, length(CAST(content AS BLOB)) AS content_bytes "
                    "FROM chunks WHERE generation_id = ? ORDER BY chunk_id LIMIT ?",
                    (current, remaining_chunks))
                matches = []
                for candidate in candidates:
                    remaining_chunks -= 1
                    size = candidate["content_bytes"]
                    if size > remaining_bytes:
                        # Stop at the byte boundary rather than skipping to
                        # later chunks and implying complete negative results.
                        remaining_bytes = 0
                        break
                    remaining_bytes -= size
                    row = self._execute(
                        "SELECT c.*, -1.0 AS rank FROM chunks AS c "
                        "WHERE generation_id = ? AND chunk_id = ?",
                        (current, candidate["chunk_id"])).fetchone()
                    content = row["content"].casefold()
                    if any(term in content for term in terms):
                        matches.append(row)
                matches.sort(key=lambda row: (row["rel_path"] or "", row["start_line"],
                             row["start_col"], row["end_line"], row["end_col"], row["chunk_id"]))
                if matches:
                    groups.append(matches[:limit])
            merged = []
            for position in range(max((len(group) for group in groups), default=0)):
                for group in groups:
                    if position < len(group):
                        merged.append(group[position])
                        if len(merged) == limit:
                            return merged
            return merged

    def search(
        self,
        query: str,
        limit: int = 20,
        generation_id: Optional[str] = None,
    ) -> List[Tuple[Chunk, float]]:
        q_clean = self._validate_fts_request(query, limit, generation_id)
        if not q_clean:
            return []
        fts_expr = self._fts_expression(q_clean)
        if fts_expr is None:
            rows = self._search_short_literal_rows(q_clean, limit, generation_id)
        else:
            rows = self._search_fts_rows(fts_expr, limit, generation_id)

        results: List[Tuple[Chunk, float]] = []
        for row in rows:
            rank = float(row["rank"])
            if not math.isfinite(rank):
                raise RuntimeError("FTS5 returned a non-finite BM25 rank")
            chunk = self._row_to_chunk(row)
            if not chunk.rel_path:
                chunk.rel_path = chunk.file_id
            results.append((chunk, -rank))
        return results

    def close(self) -> None:
        if self._closed:
            return
        self._conn.close()
        self._closed = True

    def __enter__(self) -> "ProductionSQLiteStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
