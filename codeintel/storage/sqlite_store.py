from datetime import datetime, timezone
import hashlib
import json
import threading
from typing import List, Optional, Tuple, Dict, Any
import apsw
from codeintel.core.models import (
    Entity, Relation, Chunk, Generation, FileRecord, EntityKind
)
from codeintel.storage.base import GraphStore, LexicalIndex, GenerationStore
from codeintel.storage.policy import (
    create_generation_fts, fts_expression, fts_read_snapshot, generation_fts_name,
)

# One neighbor may have several call sites. Summarize its strongest available
# evidence deterministically for the one-hop ranking boundary.
_RELATION_TRUST_RANK_SQL = (
    "CASE r.trust_class WHEN 'EXACT' THEN 0 WHEN 'RESOLVED' THEN 1 "
    "WHEN 'HEURISTIC' THEN 2 ELSE 3 END"
)
_TRUST_LABEL_SQL = "CASE {rank} WHEN 0 THEN 'EXACT' WHEN 1 THEN 'RESOLVED' WHEN 2 THEN 'HEURISTIC' ELSE 'INFERRED' END"

SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS generations (
    generation_id TEXT PRIMARY KEY,
    repo_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    snapshot_hash TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_active INTEGER DEFAULT 0,
    build_fingerprint TEXT,
    metadata_json TEXT
);

CREATE TABLE IF NOT EXISTS files (
    file_id TEXT NOT NULL,
    repo_id TEXT NOT NULL,
    generation_id TEXT NOT NULL,
    rel_path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    PRIMARY KEY (generation_id, file_id),
    FOREIGN KEY (generation_id) REFERENCES generations(generation_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS entities (
    entity_id TEXT NOT NULL,
    repo_id TEXT NOT NULL,
    file_id TEXT NOT NULL,
    generation_id TEXT NOT NULL,
    name TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    kind TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    start_col INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    end_col INTEGER NOT NULL,
    docstring TEXT,
    signature TEXT,
    properties_json TEXT,
    PRIMARY KEY (generation_id, entity_id),
    FOREIGN KEY (generation_id) REFERENCES generations(generation_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name, generation_id);
CREATE INDEX IF NOT EXISTS idx_entities_qname ON entities(qualified_name, generation_id);
CREATE INDEX IF NOT EXISTS idx_entities_file ON entities(generation_id, file_id);

CREATE TABLE IF NOT EXISTS relations (
    relation_id TEXT NOT NULL,
    repo_id TEXT NOT NULL,
    generation_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    rel_type TEXT NOT NULL,
    file_id TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    start_col INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    end_col INTEGER NOT NULL,
    trust_class TEXT NOT NULL,
    properties_json TEXT,
    PRIMARY KEY (generation_id, relation_id),
    FOREIGN KEY (generation_id) REFERENCES generations(generation_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_rel_src ON relations(generation_id, source_id, rel_type);
CREATE INDEX IF NOT EXISTS idx_rel_tgt ON relations(generation_id, target_id, rel_type);
CREATE INDEX IF NOT EXISTS idx_rel_src_tgt ON relations(generation_id, source_id, target_id);
CREATE INDEX IF NOT EXISTS idx_rel_tgt_src ON relations(generation_id, target_id, source_id);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id TEXT NOT NULL,
    file_id TEXT NOT NULL,
    generation_id TEXT NOT NULL,
    rel_path TEXT,
    start_line INTEGER NOT NULL,
    start_col INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    end_col INTEGER NOT NULL,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    entity_ids_json TEXT,
    PRIMARY KEY (generation_id, chunk_id),
    FOREIGN KEY (generation_id) REFERENCES generations(generation_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_chunks_generation_file ON chunks(generation_id, file_id);

"""

class SQLiteStore(GraphStore, LexicalIndex, GenerationStore):
    @staticmethod
    def _new_generation_id(repo_id: str, sequence: int, snapshot_hash: str) -> str:
        # Persistent IDs bind the complete repository/snapshot identity. Stored
        # legacy IDs remain opaque; only newly created generations use v2.
        payload = json.dumps(
            ["codeintel-generation-v2", repo_id, sequence, snapshot_hash],
            ensure_ascii=True, separators=(",", ":"),
        )
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        return f"gen_{sequence:06d}_{digest}"

    def __init__(self, db_path: str, *, _connection: Optional[apsw.Connection] = None):
        self.db_path = db_path
        self._lock = threading.RLock()
        self._conn = _connection if _connection is not None else apsw.Connection(self.db_path)
        self._conn.setrowtrace(self._row_factory)
        self._init_db()

    @staticmethod
    def _row_factory(cursor, row):
        return {col[0]: row[idx] for idx, col in enumerate(cursor.getdescription())}

    def _init_db(self):
        self._closed = False
        with self._lock:
            cursor = self._conn.cursor()
            for stmt in SCHEMA_SQL.split(";"):
                stmt = stmt.strip()
                if stmt:
                    cursor.execute(stmt)

    def close(self) -> None:
        with self._lock:
            if getattr(self, "_closed", False):
                return
            self._closed = True
            conn = getattr(self, "_conn", None)
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def _execute(self, sql: str, params: Tuple = ()):
        cursor = self._conn.cursor()
        cursor.execute(sql, params)
        return cursor

    def _executemany(self, sql: str, params_seq: List[Tuple]):
        cursor = self._conn.cursor()
        cursor.executemany(sql, params_seq)
        return cursor

    @staticmethod
    def _serialize_metadata(metadata: Dict[str, Any]) -> str:
        """Persistence policy hook; production overrides with its strict codec."""
        return json.dumps(metadata)

    @staticmethod
    def _load_metadata(raw: Optional[str], build_fingerprint=None) -> Dict[str, Any]:
        metadata = json.loads(raw) if raw else {}
        if build_fingerprint and "build_fingerprint" not in metadata:
            metadata["build_fingerprint"] = build_fingerprint
        return metadata

    def create_generation(
        self,
        repo_id: str,
        sequence: int,
        snapshot_hash: str,
        build_fingerprint: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Generation:
        gen_id = self._new_generation_id(repo_id, sequence, snapshot_hash)
        now_str = datetime.now(timezone.utc).isoformat()
        meta_dict = dict(metadata or {})
        if build_fingerprint:
            meta_dict["build_fingerprint"] = build_fingerprint
        meta_json = self._serialize_metadata(meta_dict)

        with self._lock:
            self._conn.execute("SAVEPOINT codeintel_generation_create")
            try:
                self._execute("""
                    INSERT INTO generations (generation_id, repo_id, sequence, snapshot_hash, created_at, is_active, build_fingerprint, metadata_json)
                    VALUES (?, ?, ?, ?, ?, 0, ?, ?)
                """, (gen_id, repo_id, sequence, snapshot_hash, now_str, build_fingerprint, meta_json))
                create_generation_fts(self._conn, gen_id)
                self._conn.execute("RELEASE SAVEPOINT codeintel_generation_create")
            except BaseException:
                if self._conn.in_transaction:
                    self._conn.execute("ROLLBACK TO SAVEPOINT codeintel_generation_create")
                    self._conn.execute("RELEASE SAVEPOINT codeintel_generation_create")
                raise
            return Generation(
                generation_id=gen_id,
                repo_id=repo_id,
                sequence=sequence,
                snapshot_hash=snapshot_hash,
                created_at=now_str,
                is_active=False,
                metadata=meta_dict
            )

    def update_generation_metadata(self, generation_id: str, metadata: Dict[str, Any]) -> None:
        with self._lock:
            cur = self._execute("SELECT metadata_json, build_fingerprint FROM generations WHERE generation_id = ?", (generation_id,))
            row = cur.fetchone()
            if row:
                existing = self._load_metadata(row["metadata_json"])
                existing.update(metadata)
                if "build_fingerprint" not in existing and row["build_fingerprint"]:
                    existing["build_fingerprint"] = row["build_fingerprint"]
                new_build_fp = existing.get("build_fingerprint")
                if new_build_fp is not None:
                    self._execute(
                        "UPDATE generations SET metadata_json = ?, build_fingerprint = ? WHERE generation_id = ?",
                        (self._serialize_metadata(existing), new_build_fp, generation_id)
                    )
                else:
                    self._execute(
                        "UPDATE generations SET metadata_json = ? WHERE generation_id = ?",
                        (self._serialize_metadata(existing), generation_id)
                    )

    def activate_generation(self, generation_id: str) -> None:
        with self._lock:
            cur = self._execute("SELECT repo_id FROM generations WHERE generation_id = ?", (generation_id,))
            row = cur.fetchone()
            if not row:
                raise ValueError(f"Generation {generation_id} does not exist.")
            repo_id = row["repo_id"]

            cursor = self._conn.cursor()
            cursor.execute("BEGIN IMMEDIATE")
            try:
                cursor.execute("UPDATE generations SET is_active = 0 WHERE repo_id = ?", (repo_id,))
                cursor.execute("UPDATE generations SET is_active = 1 WHERE generation_id = ?", (generation_id,))
                cursor.execute("COMMIT")
            except BaseException:
                if self._conn.in_transaction:
                    cursor.execute("ROLLBACK")
                raise

    def delete_generation_data(self, generation_id: str) -> None:
        table = generation_fts_name(generation_id)
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("BEGIN IMMEDIATE")
            try:
                cursor.execute(f'DROP TABLE IF EXISTS "{table}"')
                cursor.execute("DELETE FROM chunks WHERE generation_id = ?", (generation_id,))
                cursor.execute("DELETE FROM relations WHERE generation_id = ?", (generation_id,))
                cursor.execute("DELETE FROM entities WHERE generation_id = ?", (generation_id,))
                cursor.execute("DELETE FROM files WHERE generation_id = ?", (generation_id,))
                cursor.execute("DELETE FROM generations WHERE generation_id = ?", (generation_id,))
                cursor.execute("COMMIT")
            except BaseException:
                if self._conn.in_transaction:
                    cursor.execute("ROLLBACK")
                raise

    def remove_generation_data(self, generation_id: str) -> None:
        self.delete_generation_data(generation_id)

    def get_active_generation(self, repo_id: str) -> Optional[Generation]:
        with self._lock:
            cur = self._execute("""
                SELECT generation_id, repo_id, sequence, snapshot_hash, created_at, is_active, build_fingerprint, metadata_json
                FROM generations
                WHERE repo_id = ? AND is_active = 1
            """, (repo_id,))
            row = cur.fetchone()
            if row:
                meta = self._load_metadata(
                    row.get("metadata_json"), row.get("build_fingerprint")
                )
                return Generation(
                    generation_id=row["generation_id"],
                    repo_id=row["repo_id"],
                    sequence=row["sequence"],
                    snapshot_hash=row["snapshot_hash"],
                    created_at=str(row["created_at"]),
                    is_active=bool(row["is_active"]),
                    metadata=meta
                )
            return None

    def list_generations(self, repo_id: str) -> List[Generation]:
        with self._lock:
            cur = self._execute("""
                SELECT generation_id, repo_id, sequence, snapshot_hash, created_at, is_active, build_fingerprint, metadata_json
                FROM generations
                WHERE repo_id = ?
                ORDER BY sequence DESC
            """, (repo_id,))
            results = []
            for r in cur.fetchall():
                meta = self._load_metadata(
                    r.get("metadata_json"), r.get("build_fingerprint")
                )
                results.append(Generation(
                    generation_id=r["generation_id"],
                    repo_id=r["repo_id"],
                    sequence=r["sequence"],
                    snapshot_hash=r["snapshot_hash"],
                    created_at=str(r["created_at"]),
                    is_active=bool(r["is_active"]),
                    metadata=meta
                ))
            return results

    def add_entities(self, entities: List[Entity]) -> None:
        if not entities:
            return
        rows = [
            (
                e.entity_id, e.repo_id, e.file_id, e.generation_id, e.name, e.qualified_name,
                e.kind.value, e.span[0], e.span[1], e.span[2], e.span[3],
                e.docstring, e.signature, json.dumps(e.properties)
            )
            for e in entities
        ]
        with self._lock:
            self._executemany("""
                INSERT OR REPLACE INTO entities
                (entity_id, repo_id, file_id, generation_id, name, qualified_name, kind,
                 start_line, start_col, end_line, end_col, docstring, signature, properties_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, rows)

    def add_relations(self, relations: List[Relation]) -> None:
        if not relations:
            return
        rows = [
            (
                r.relation_id, r.repo_id, r.generation_id, r.source_id, r.target_id,
                r.rel_type.value, r.file_id, r.span[0], r.span[1], r.span[2], r.span[3],
                r.trust_class.value, json.dumps(r.properties)
            )
            for r in relations
        ]
        with self._lock:
            self._executemany("""
                INSERT OR REPLACE INTO relations
                (relation_id, repo_id, generation_id, source_id, target_id, rel_type,
                 file_id, start_line, start_col, end_line, end_col, trust_class, properties_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, rows)

    def add_files(self, files: List[Any]) -> None:
        if not files:
            return
        rows = [
            (f.file_id, f.repo_id, f.generation_id, f.rel_path, f.content_hash, f.size_bytes)
            for f in files
        ]
        with self._lock:
            self._executemany("""
                INSERT OR REPLACE INTO files (file_id, repo_id, generation_id, rel_path, content_hash, size_bytes)
                VALUES (?, ?, ?, ?, ?, ?)
            """, rows)

    def get_files(self, generation_id: str) -> List[FileRecord]:
        with self._lock:
            cur = self._execute("SELECT * FROM files WHERE generation_id = ?", (generation_id,))
            return [
                FileRecord(
                    file_id=r["file_id"],
                    repo_id=r["repo_id"],
                    generation_id=r["generation_id"],
                    rel_path=r["rel_path"],
                    content_hash=r["content_hash"],
                    size_bytes=r["size_bytes"]
                )
                for r in cur.fetchall()
            ]

    def get_file_hashes(self, generation_id: str) -> Dict[str, str]:
        with self._lock:
            cur = self._execute("SELECT rel_path, content_hash FROM files WHERE generation_id = ?", (generation_id,))
            return {r["rel_path"]: r["content_hash"] for r in cur.fetchall()}

    def get_file_record(self, file_id: str, *, generation_id: str) -> Optional[FileRecord]:
        """One exact generation-bound file identity, without scanning the repository."""
        with self._lock:
            row = self._execute(
                "SELECT * FROM files WHERE file_id = ? AND generation_id = ?",
                (file_id, generation_id),
            ).fetchone()
            if row is None:
                return None
            return FileRecord(
                file_id=row["file_id"], repo_id=row["repo_id"],
                generation_id=row["generation_id"], rel_path=row["rel_path"],
                content_hash=row["content_hash"], size_bytes=row["size_bytes"],
            )

    def get_entity(self, entity_id: str, generation_id: Optional[str] = None) -> Optional[Entity]:
        with self._lock:
            if generation_id:
                cur = self._execute("""
                    SELECT * FROM entities
                    WHERE entity_id = ? AND generation_id = ?
                """, (entity_id, generation_id))
            else:
                cur = self._execute("""
                    SELECT * FROM entities
                    WHERE entity_id = ? AND generation_id IN (SELECT generation_id FROM generations WHERE is_active = 1)
                """, (entity_id,))
            row = cur.fetchone()
            if row:
                return self._row_to_entity(row)
            return None

    def get_entities_by_name(self, name: str, repo_id: Optional[str] = None, generation_id: Optional[str] = None) -> List[Entity]:
        clauses = ["(e.name = ? OR e.qualified_name = ?)"]
        params = [name, name]
        if repo_id is not None:
            clauses.append("e.repo_id = ?")
            params.append(repo_id)
        if generation_id is not None:
            clauses.append("e.generation_id = ?")
            params.append(generation_id)
        else:
            clauses.append("g.is_active = 1")
        with self._lock:
            cur = self._execute(
                "SELECT e.* FROM entities e JOIN generations g "
                "ON g.generation_id = e.generation_id AND g.repo_id = e.repo_id "
                "WHERE " + " AND ".join(clauses) +
                " ORDER BY e.generation_id, e.qualified_name, e.entity_id",
                tuple(params),
            )
            return [self._row_to_entity(row) for row in cur.fetchall()]

    def _get_retrieval_entities(
        self, name: str, generation_id: str, *, limit: int,
        qualified: bool = False, suffix: Optional[str] = None,
    ) -> List[Entity]:
        """Acquire a bounded exact-match prefix in the retriever's ranking order.

        This limits Python row materialization, not SQLite scan/sort work. Keep
        the general-purpose name getters unchanged for existing callers.
        """
        if type(limit) is not int or limit < 1:
            raise ValueError("entity acquisition limit must be a positive integer")
        match = "e.qualified_name = ?" if qualified else "(e.name = ? OR e.qualified_name = ?)"
        params = [name] if qualified else [name, name]
        if suffix is not None:
            match += " AND substr(e.qualified_name, -length(?)) = ?"
            params.extend([suffix, suffix])
        params.extend([generation_id, limit])
        # Indexed entities normally carry rel_path. Match exact_sort's fallback
        # to the generation-bound file table when that hint is absent.
        path = "COALESCE(NULLIF(json_extract(e.properties_json, '$.rel_path'), ''), f.rel_path, '~')"
        lower = f"lower({path})"
        test = " OR ".join(f"instr({lower}, '{part}') > 0" for part in
                           ("/test", "test_", "_test", "tests/"))
        with self._lock:
            rows = self._execute(
                "SELECT e.* FROM entities e JOIN generations g "
                "ON g.generation_id = e.generation_id AND g.repo_id = e.repo_id "
                "LEFT JOIN files f ON f.generation_id = e.generation_id AND f.file_id = e.file_id "
                f"WHERE {match} AND e.generation_id = ? "
                f"ORDER BY CASE WHEN {test} THEN 1 ELSE 0 END, {path}, "
                "e.start_line, e.qualified_name, e.entity_id LIMIT ?", tuple(params))
            return [self._row_to_entity(row) for row in rows]

    def get_file_path(self, file_id: str, generation_id: Optional[str] = None) -> Optional[str]:
        with self._lock:
            if generation_id:
                cur = self._execute("SELECT rel_path FROM files WHERE file_id = ? AND generation_id = ?", (file_id, generation_id))
            else:
                cur = self._execute("SELECT rel_path FROM files WHERE file_id = ? AND generation_id IN (SELECT generation_id FROM generations WHERE is_active = 1)", (file_id,))
            row = cur.fetchone()
            if row:
                return row["rel_path"]
            return None

    def get_entities_by_qualified_name(self, qualified_name: str, generation_id: Optional[str] = None) -> List[Entity]:
        with self._lock:
            if generation_id:
                cur = self._execute("SELECT * FROM entities WHERE qualified_name = ? AND generation_id = ?", (qualified_name, generation_id))
            else:
                cur = self._execute("SELECT * FROM entities WHERE qualified_name = ? AND generation_id IN (SELECT generation_id FROM generations WHERE is_active = 1)", (qualified_name,))
            return [self._row_to_entity(r) for r in cur.fetchall()]


    def _row_to_entity(self, row: Dict[str, Any]) -> Entity:
        return Entity(
            entity_id=row["entity_id"],
            repo_id=row["repo_id"],
            file_id=row["file_id"],
            generation_id=row["generation_id"],
            name=row["name"],
            qualified_name=row["qualified_name"],
            kind=EntityKind(row["kind"]),
            span=(row["start_line"], row["start_col"], row["end_line"], row["end_col"]),
            docstring=row["docstring"],
            signature=row["signature"],
            properties=json.loads(row["properties_json"]) if row["properties_json"] else {}
        )


    def get_files_count(self, generation_id: Optional[str] = None) -> int:
        with self._lock:
            if generation_id:
                cur = self._execute("SELECT COUNT(*) as count FROM files WHERE generation_id = ?", (generation_id,))
            else:
                cur = self._execute("SELECT COUNT(*) as count FROM files WHERE generation_id IN (SELECT generation_id FROM generations WHERE is_active = 1)")
            row = cur.fetchone()
            return int(row["count"]) if row and "count" in row else 0

    def get_callers(
        self,
        entity_id: str,
        max_depth: int = 1,
        generation_id: Optional[str] = None,
        include_heuristic: bool = False
    ) -> List[Tuple[int, Entity, str]]:
        if type(max_depth) is not int or max_depth != 1:
            raise ValueError("Only one-hop graph neighbors are supported")
        with self._lock:
            gen_clause = "r.generation_id = ?" if generation_id else "r.generation_id IN (SELECT generation_id FROM generations WHERE is_active = 1)"
            gen_clause_e = "e.generation_id = ?" if generation_id else "e.generation_id IN (SELECT generation_id FROM generations WHERE is_active = 1)"
            trust_clause = "AND r.trust_class IN ('EXACT', 'RESOLVED')" if not include_heuristic else ""

            # Keep class-member seeds in SQLite. Materializing every member
            # into a Python list/placeholder string bypassed the neighbor cap.
            params = [entity_id, entity_id]
            if generation_id:
                params.extend([generation_id, generation_id, generation_id])
            seeds = "SELECT seed_id FROM seed_ids"
            sql = f"""
                WITH seed_ids(seed_id) AS (
                    SELECT ? UNION SELECT target_id FROM relations r
                    WHERE r.source_id = ? AND r.rel_type = 'DEFINES' AND {gen_clause}
                )
                SELECT e.*, {_TRUST_LABEL_SQL.format(rank="MIN(" + _RELATION_TRUST_RANK_SQL + ")")} as rel_trust, 1 as min_depth
                FROM entities e
                JOIN relations r ON e.entity_id = r.source_id
                WHERE r.target_id IN ({seeds}) AND r.rel_type IN ('CALLS', 'REFERENCES') {trust_clause} AND {gen_clause}
                  AND {gen_clause_e} AND e.entity_id NOT IN ({seeds})
                GROUP BY e.entity_id
                LIMIT 50
            """
            cur = self._execute(sql, tuple(params))
            return [(r["min_depth"], self._row_to_entity(r), r.get("rel_trust", "RESOLVED")) for r in cur.fetchall()]

    def get_callees(
        self,
        entity_id: str,
        max_depth: int = 1,
        generation_id: Optional[str] = None,
        include_heuristic: bool = False
    ) -> List[Tuple[int, Entity, str]]:
        if type(max_depth) is not int or max_depth != 1:
            raise ValueError("Only one-hop graph neighbors are supported")
        with self._lock:
            gen_clause = "r.generation_id = ?" if generation_id else "r.generation_id IN (SELECT generation_id FROM generations WHERE is_active = 1)"
            gen_clause_e = "e.generation_id = ?" if generation_id else "e.generation_id IN (SELECT generation_id FROM generations WHERE is_active = 1)"
            trust_clause = "AND r.trust_class IN ('EXACT', 'RESOLVED')" if not include_heuristic else ""

            # Keep class-member seeds in SQLite. Materializing every member
            # into a Python list/placeholder string bypassed the neighbor cap.
            params = [entity_id, entity_id]
            if generation_id:
                params.extend([generation_id, generation_id, generation_id])
            seeds = "SELECT seed_id FROM seed_ids"
            sql = f"""
                WITH seed_ids(seed_id) AS (
                    SELECT ? UNION SELECT target_id FROM relations r
                    WHERE r.source_id = ? AND r.rel_type = 'DEFINES' AND {gen_clause}
                )
                SELECT e.*, {_TRUST_LABEL_SQL.format(rank="MIN(" + _RELATION_TRUST_RANK_SQL + ")")} as rel_trust, 1 as min_depth
                FROM entities e
                JOIN relations r ON e.entity_id = r.target_id
                WHERE r.source_id IN ({seeds}) AND r.rel_type IN ('CALLS', 'REFERENCES') {trust_clause} AND {gen_clause}
                  AND {gen_clause_e} AND e.entity_id NOT IN ({seeds})
                GROUP BY e.entity_id
                LIMIT 50
            """
            cur = self._execute(sql, tuple(params))
            return [(r["min_depth"], self._row_to_entity(r), r.get("rel_trust", "RESOLVED")) for r in cur.fetchall()]


    def index_chunks(self, chunks: List[Chunk], is_full_replacement: bool = True) -> None:
        if not chunks:
            return
        c_rows = [
            (
                c.chunk_id, c.file_id, c.generation_id, c.rel_path,
                c.span[0], c.span[1], c.span[2], c.span[3],
                c.content, c.content_hash, json.dumps(c.entity_ids)
            )
            for c in chunks
        ]
        fts_rows = [
            (c.chunk_id, c.file_id, c.generation_id, c.content)
            for c in chunks
        ]
        gen_id = chunks[0].generation_id
        table = generation_fts_name(gen_id)
        chunk_ids = [c.chunk_id for c in chunks]
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("BEGIN IMMEDIATE")
            try:
                if is_full_replacement:
                    cursor.execute(f'DELETE FROM "{table}"')
                    cursor.execute("DELETE FROM chunks WHERE generation_id = ?", (gen_id,))
                else:
                    cursor.execute("CREATE TEMP TABLE IF NOT EXISTS _del_cids (cid TEXT PRIMARY KEY)")
                    cursor.execute("DELETE FROM _del_cids")
                    cursor.executemany("INSERT OR IGNORE INTO _del_cids (cid) VALUES (?)", [(cid,) for cid in chunk_ids])
                    cursor.execute(f"""
                        DELETE FROM "{table}"
                        WHERE generation_id = ? AND chunk_id IN (SELECT cid FROM _del_cids)
                    """, (gen_id,))
                    cursor.execute("""
                        DELETE FROM chunks
                        WHERE generation_id = ? AND chunk_id IN (SELECT cid FROM _del_cids)
                    """, (gen_id,))
                    cursor.execute("DELETE FROM _del_cids")

                cursor.executemany("""
                    INSERT OR REPLACE INTO chunks
                    (chunk_id, file_id, generation_id, rel_path, start_line, start_col, end_line, end_col,
                     content, content_hash, entity_ids_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, c_rows)
                cursor.executemany(f"""
                    INSERT OR REPLACE INTO "{table}" (chunk_id, file_id, generation_id, content)
                    VALUES (?, ?, ?, ?)
                """, fts_rows)
                cursor.execute("COMMIT")
            except BaseException:
                if self._conn.in_transaction:
                    cursor.execute("ROLLBACK")
                raise

    def get_all_fts_chunk_ids(self, generation_id: str) -> List[str]:
        with self._lock:
            table = generation_fts_name(generation_id)
            if self._execute("SELECT 1 FROM generations WHERE generation_id = ?", (generation_id,)).fetchone() is None:
                return []
            cur = self._execute(f'SELECT chunk_id FROM "{table}" WHERE generation_id = ?', (generation_id,))
            return [r["chunk_id"] if "chunk_id" in r.keys() else list(r.values())[0] for r in cur.fetchall()]

    def _search_fts_rows(self, fts_expr: str, limit: int, generation_id: Optional[str]):
        """Rank within each corpus. Unscoped reads round-robin active repos.

        BM25 scores from different corpora are deliberately never compared or
        summed. The maintained service always supplies its pinned generation.
        """
        with self._lock, fts_read_snapshot(self._conn):
            if generation_id is None:
                scopes = self._execute(
                    "SELECT generation_id FROM generations WHERE is_active = 1 "
                    "ORDER BY repo_id, generation_id").fetchall()
            else:
                generation_fts_name(generation_id)  # validate even a missing ID
                scopes = self._execute("SELECT generation_id FROM generations WHERE generation_id = ?",
                                       (generation_id,)).fetchall()
            groups = []
            for scope in scopes:
                current = scope["generation_id"]
                table = generation_fts_name(current)
                rows = self._execute(f"""
                    SELECT c.*, bm25("{table}") AS rank
                    FROM "{table}" AS fts JOIN chunks AS c
                      ON fts.chunk_id = c.chunk_id AND fts.generation_id = c.generation_id
                    WHERE "{table}" MATCH ? AND fts.generation_id = ?
                    ORDER BY rank ASC, COALESCE(c.rel_path, '') ASC,
                             c.start_line ASC, c.start_col ASC, c.chunk_id ASC
                    LIMIT ?
                    """, (fts_expr, current, limit)).fetchall()
                if rows:
                    groups.append(rows)
            merged = []
            for rank in range(max((len(group) for group in groups), default=0)):
                for group in groups:
                    if rank < len(group):
                        merged.append(group[rank])
                        if len(merged) == limit:
                            return merged
            return merged

    def search(self, query: str, limit: int = 20, generation_id: Optional[str] = None) -> List[Tuple[Chunk, float]]:
        with self._lock:
            # Build clean FTS5 trigram query
            q_clean = query.replace('"', ' ').replace('*', ' ').strip()
            if not q_clean:
                return []

            import re
            tokens = [t.strip() for t in re.findall(r'[a-zA-Z0-9_/.-]+', q_clean) if len(t.strip()) >= 3]
            if not tokens:
                # Fallback to alphanumeric tokens
                tokens = [t.strip() for t in re.findall(r'[a-zA-Z0-9_]+', q_clean) if len(t.strip()) >= 2]
            if not tokens:
                return []

            fts_expr = fts_expression(tokens)

            rows = self._search_fts_rows(fts_expr, limit, generation_id)

            results = []
            for r in rows:
                chunk = Chunk(
                    chunk_id=r["chunk_id"],
                    file_id=r["file_id"],
                    generation_id=r["generation_id"],
                    rel_path=r["rel_path"] if "rel_path" in r.keys() else r["file_id"],
                    span=(r["start_line"], r["start_col"], r["end_line"], r["end_col"]),
                    content=r["content"],
                    content_hash=r["content_hash"],
                    entity_ids=json.loads(r["entity_ids_json"]) if r["entity_ids_json"] else []
                )
                score = -float(r["rank"])
                results.append((chunk, score))
            return results

    def get_chunk(self, chunk_id: str, generation_id: Optional[str] = None) -> Optional[Chunk]:
        with self._lock:
            if generation_id:
                cur = self._execute("""
                    SELECT * FROM chunks
                    WHERE chunk_id = ? AND generation_id = ?
                """, (chunk_id, generation_id))
            else:
                cur = self._execute("""
                    SELECT * FROM chunks
                    WHERE chunk_id = ? AND generation_id IN (SELECT generation_id FROM generations WHERE is_active = 1)
                """, (chunk_id,))
            r = cur.fetchone()
            if not r:
                return None
            return Chunk(
                chunk_id=r["chunk_id"],
                file_id=r["file_id"],
                generation_id=r["generation_id"],
                rel_path=r["rel_path"] if "rel_path" in r.keys() else r["file_id"],
                span=(r["start_line"], r["start_col"], r["end_line"], r["end_col"]),
                content=r["content"],
                content_hash=r["content_hash"],
                entity_ids=json.loads(r["entity_ids_json"]) if r["entity_ids_json"] else []
            )

    def _iter_retrieval_chunks(self, generation_id: str):
        """Stream one generation without materializing all IDs or source bodies."""
        with self._lock:
            rows = self._execute("SELECT * FROM chunks WHERE generation_id = ? ORDER BY chunk_id",
                                 (generation_id,))
            for row in rows:
                yield self._row_to_chunk(row)

    @staticmethod
    def _row_to_chunk(row) -> Chunk:
        return Chunk(
            chunk_id=row["chunk_id"], file_id=row["file_id"],
            generation_id=row["generation_id"], rel_path=row["rel_path"],
            span=(row["start_line"], row["start_col"], row["end_line"], row["end_col"]),
            content=row["content"], content_hash=row["content_hash"],
            entity_ids=json.loads(row["entity_ids_json"]) if row["entity_ids_json"] else [],
        )

    def _get_retrieval_chunks(
        self, entity: Entity, generation_id: str, *, limit: int,
        max_bytes: int, preferred_only: bool = False,
    ) -> List[Chunk]:
        """Read bounded literal bodies for a symbol, never every chunk in its file.

        Metadata lengths are inspected before source text enters Python. The
        limit does not bound SQLite's internal scan, JSON, or sort work.
        """
        if type(limit) is not int or limit < 1 or type(max_bytes) is not int or max_bytes < 0:
            raise ValueError("chunk acquisition requires positive rows and non-negative bytes")
        if entity.generation_id != generation_id:
            return []
        start_line, start_col, end_line, end_col = entity.span
        params = [entity.file_id, generation_id]
        if preferred_only:
            where = "EXISTS (SELECT 1 FROM json_each(c.entity_ids_json) WHERE value = ?)"
            params.append(entity.entity_id)
            order = (
                "CASE WHEN (start_line, start_col, end_line, end_col) = (?, ?, ?, ?) THEN 0 ELSE 1 END, "
                "CASE WHEN (start_line, start_col) <= (?, ?) AND (end_line, end_col) >= (?, ?) THEN 0 ELSE 1 END, "
                "max(0, end_line - start_line), length(CAST(content AS BLOB)), start_line, chunk_id"
            )
            params.extend([*entity.span, start_line, start_col, start_line, start_col])
            limit = 1
        else:
            where = "(start_line, start_col) < (?, ?) AND (end_line, end_col) > (?, ?)"
            params.extend([end_line, end_col, start_line, start_col])
            order = "start_line, start_col, end_line, end_col, chunk_id"
        params.append(limit)
        with self._lock, fts_read_snapshot(self._conn):
            candidates = self._execute(
                "SELECT chunk_id, length(CAST(content AS BLOB)) AS content_bytes FROM chunks c "
                f"WHERE file_id = ? AND generation_id = ? AND {where} ORDER BY {order} LIMIT ?",
                tuple(params))
            chunks = []
            for candidate in candidates:
                if candidate["content_bytes"] > max_bytes:
                    break
                max_bytes -= candidate["content_bytes"]
                row = self._execute("SELECT * FROM chunks WHERE generation_id = ? AND chunk_id = ?",
                                    (generation_id, candidate["chunk_id"])).fetchone()
                if row is not None:
                    chunks.append(self._row_to_chunk(row))
            return chunks

    def get_chunks_for_file(self, file_id: str, generation_id: Optional[str] = None) -> List[Chunk]:
        with self._lock:
            if generation_id:
                cur = self._execute("""
                    SELECT * FROM chunks
                    WHERE file_id = ? AND generation_id = ?
                """, (file_id, generation_id))
            else:
                cur = self._execute("""
                    SELECT * FROM chunks
                    WHERE file_id = ? AND generation_id IN (SELECT generation_id FROM generations WHERE is_active = 1)
                """, (file_id,))
            rows = cur.fetchall()
            return [
                Chunk(
                    chunk_id=r["chunk_id"],
                    file_id=r["file_id"],
                    generation_id=r["generation_id"],
                    rel_path=r["rel_path"] if "rel_path" in r.keys() else r["file_id"],
                    span=(r["start_line"], r["start_col"], r["end_line"], r["end_col"]),
                    content=r["content"],
                    content_hash=r["content_hash"],
                    entity_ids=json.loads(r["entity_ids_json"]) if r["entity_ids_json"] else []
                )
                for r in rows
            ]

    def get_entities_count(self, generation_id: str) -> int:
        with self._lock:
            cur = self._execute("SELECT count(*) as cnt FROM entities WHERE generation_id = ?", (generation_id,))
            row = cur.fetchone()
            if not row:
                return 0
            return int(row["cnt"] if "cnt" in row.keys() else list(row.values())[0])


    def get_entities_for_generation(self, generation_id: str) -> List[Entity]:
        with self._lock:
            cur = self._execute("SELECT * FROM entities WHERE generation_id = ?", (generation_id,))
            return [self._row_to_entity(r) for r in cur.fetchall()]

    def get_all_chunk_ids(self, generation_id: str) -> List[str]:
        with self._lock:
            cur = self._execute("SELECT chunk_id FROM chunks WHERE generation_id = ?", (generation_id,))
            return [r["chunk_id"] if "chunk_id" in r.keys() else list(r.values())[0] for r in cur.fetchall()]

    def get_relations_count(self, generation_id: str) -> int:
        with self._lock:
            cur = self._execute("SELECT count(*) as cnt FROM relations WHERE generation_id = ?", (generation_id,))
            row = cur.fetchone()
            if not row:
                return 0
            return int(row["cnt"] if "cnt" in row.keys() else list(row.values())[0])
