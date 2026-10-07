"""First-class SQLite policy for CodeIntel-owned derived state."""

from pathlib import Path
from contextlib import contextmanager
import hashlib
import re
from typing import Dict, Set

import apsw

from codeintel.core.security import MAX_STATE_LAYOUT_MARKER_BYTES, STATE_LAYOUT_VERSION
from codeintel.safe_artifacts import read_regular_file_bounded

class DerivedStateValidationError(RuntimeError):
    """Expected incompatible/corrupt derived state; safe to present CLI recovery."""


CODEINTEL_DB_APPLICATION_ID = 0x43494E54  # ASCII-ish "CINT"
CODEINTEL_DB_USER_VERSION = 2
CODEINTEL_DB_BUSY_TIMEOUT_MS = 1000

_REQUIRED_SCHEMA: Dict[str, Set[str]] = {
    "generations": {
        "generation_id", "repo_id", "sequence", "snapshot_hash", "created_at",
        "is_active", "build_fingerprint", "metadata_json"
    },
    "files": {
        "file_id", "repo_id", "generation_id", "rel_path", "content_hash", "size_bytes"
    },
    "entities": {
        "entity_id", "repo_id", "file_id", "generation_id", "name", "qualified_name", "kind",
        "start_line", "start_col", "end_line", "end_col", "docstring", "signature", "properties_json"
    },
    "relations": {
        "relation_id", "repo_id", "generation_id", "source_id", "target_id", "rel_type", "trust_class",
        "file_id", "start_line", "start_col", "end_line", "end_col", "properties_json"
    },
    "chunks": {
        "chunk_id", "file_id", "generation_id", "rel_path", "content", "content_hash",
        "start_line", "start_col", "end_line", "end_col", "entity_ids_json"
    },
}
_REQUIRED_GENERATION_FK_TABLES = {"files", "entities", "relations", "chunks"}
_REQUIRED_PRIMARY_KEYS = {
    "generations": ("generation_id",),
    "files": ("generation_id", "file_id"),
    "entities": ("generation_id", "entity_id"),
    "relations": ("generation_id", "relation_id"),
    "chunks": ("generation_id", "chunk_id"),
}



def is_codeintel_db(connection: apsw.Connection) -> bool:
    """Return whether a connection points at a real, versioned CodeIntel state database."""
    try:
        filename = Path(connection.db_filename("main")).resolve()
        if filename.name != "db.sqlite":
            return False
        marker = filename.parent / ".codeintel_state_layout"
        if not (filename.parent / ".codeintel_state_layout").is_file():
            return False
        raw = read_regular_file_bounded(
            marker,
            max_bytes=MAX_STATE_LAYOUT_MARKER_BYTES,
            label="CodeIntel state-layout marker",
        )
        version = raw.decode("utf-8", errors="strict").strip()
    except Exception:
        return False
    return version in {STATE_LAYOUT_VERSION, "codeintel-state-v1"}


def _row_col(row, index: int, name: str | None = None):
    if isinstance(row, dict):
        if name is not None and name in row:
            return row[name]
        return list(row.values())[index]
    return row[index]


def table_columns(connection: apsw.Connection, table: str) -> Set[str]:
    escaped = table.replace('"', '""')
    return {
        str(_row_col(row, 1, "name"))
        for row in connection.execute(f'PRAGMA table_info("{escaped}")')
    }


def _validate_primary_keys(connection: apsw.Connection) -> None:
    # INSERT OR REPLACE relies on these identity constraints. Matching column
    # names alone must not admit a table that silently stores duplicate IDs.
    for table, expected in _REQUIRED_PRIMARY_KEYS.items():
        rows = connection.execute(f'PRAGMA table_info("{table}")')
        observed = {str(_row_col(row, 1, "name")): int(_row_col(row, 5, "pk"))
                    for row in rows if int(_row_col(row, 5, "pk"))}
        if observed != {name: index for index, name in enumerate(expected, 1)}:
            raise DerivedStateValidationError(
                f"CodeIntel table {table!r} has incompatible primary key; rebuild required")


def _validate_generation_foreign_keys(connection: apsw.Connection) -> None:
    for table in sorted(_REQUIRED_GENERATION_FK_TABLES):
        escaped = table.replace('"', '""')
        rows = list(connection.execute(f'PRAGMA foreign_key_list("{escaped}")'))
        has_generation_fk = False
        for row in rows:
            referenced_table = str(_row_col(row, 2, "table"))
            from_col = str(_row_col(row, 3, "from"))
            to_col = str(_row_col(row, 4, "to"))
            on_delete = str(_row_col(row, 6, "on_delete")).upper()
            if (
                referenced_table == "generations"
                and from_col == "generation_id"
                and to_col == "generation_id"
                and on_delete == "CASCADE"
            ):
                has_generation_fk = True
                break
        if not has_generation_fk:
            raise DerivedStateValidationError(
                "CodeIntel derived-state schema is incompatible; rebuild required. "
                f"Table {table!r} lacks the canonical generation foreign key"
            )


def fts_expression(tokens: list[str]) -> str | None:
    """Build the shared quoted phrase/OR syntax from lexical-policy tokens."""
    if not tokens:
        return None
    quoted = [f'"{token}"' for token in tokens]
    if len(tokens) == 1:
        return quoted[0]
    return '"' + " ".join(tokens) + '" OR (' + " OR ".join(quoted) + ")"


def generation_fts_name(generation_id: str) -> str:
    """Return an identifier derived only from a validated internal generation ID."""
    if not isinstance(generation_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", generation_id):
        raise ValueError("Unsafe generation ID for FTS table")
    return "chunks_fts_" + hashlib.sha256(generation_id.encode("ascii")).hexdigest()


def generation_fts_sql(generation_id: str) -> str:
    table = generation_fts_name(generation_id)
    return (f'CREATE VIRTUAL TABLE "{table}" USING fts5('
            "chunk_id UNINDEXED, file_id UNINDEXED, generation_id UNINDEXED, "
            "content, tokenize='trigram')")


def create_generation_fts(connection, generation_id: str) -> None:
    connection.execute(generation_fts_sql(generation_id))


@contextmanager
def fts_read_snapshot(connection):
    """Keep dynamic per-generation schema/data checks in one SQLite snapshot."""
    owned = not connection.in_transaction
    if owned:
        connection.execute("SAVEPOINT codeintel_fts_read")
    try:
        yield
    finally:
        if owned and connection.in_transaction:
            connection.execute("RELEASE SAVEPOINT codeintel_fts_read")


def _validate_fts_schema(connection: apsw.Connection) -> None:
    generations = [str(_row_col(row, 0, "generation_id")) for row in connection.execute(
        "SELECT generation_id FROM generations ORDER BY generation_id")]
    expected = set()
    for generation_id in generations:
        try:
            table = generation_fts_name(generation_id)
        except ValueError as exc:
            raise DerivedStateValidationError("Invalid generation FTS identity") from exc
        expected.add(table)
        row = connection.execute("SELECT type, sql FROM sqlite_schema WHERE name = ?", (table,)).fetchone()
        if row is None:
            raise DerivedStateValidationError("Missing generation FTS table; use a new state directory")
        obj_type = str(_row_col(row, 0, "type")).lower()
        normalized = " ".join(str(_row_col(row, 1, "sql") or "").lower().split())
        if obj_type != "table" or "virtual table" not in normalized or "using fts5" not in normalized:
            raise DerivedStateValidationError("Generation index is not the canonical FTS5 virtual table")
        if "tokenize" not in normalized or "trigram" not in normalized:
            raise DerivedStateValidationError("Generation FTS table requires trigram tokenization")
        if normalized != " ".join(generation_fts_sql(generation_id).lower().split()):
            raise DerivedStateValidationError("Generation FTS table has incompatible canonical options")
    actual = {str(_row_col(row, 0, "name")) for row in connection.execute(
        "SELECT name FROM sqlite_schema WHERE type IN ('table','view')")
        if re.fullmatch(r"chunks_fts_[0-9a-f]{64}", str(_row_col(row, 0, "name")))}
    if actual != expected:
        raise DerivedStateValidationError("Orphan or missing generation FTS table")
    if connection.execute("SELECT 1 FROM sqlite_schema WHERE name = 'chunks_fts'").fetchone():
        raise DerivedStateValidationError("Legacy global FTS state is incompatible; use a new state directory")


def validate_legacy_schema(connection: apsw.Connection) -> None:
    tables = {
        str(_row_col(row, 0, "name"))
        for row in connection.execute(
            "SELECT name FROM sqlite_schema WHERE type IN ('table','view')"
        )
        if not str(_row_col(row, 0, "name")).startswith("sqlite_")
    }
    if not tables:
        return
    missing_tables = set(_REQUIRED_SCHEMA) - tables
    if missing_tables:
        raise DerivedStateValidationError(
            "CodeIntel derived-state schema is incomplete; rebuild required. "
            f"Missing tables: {sorted(missing_tables)}"
        )
    for table, required in _REQUIRED_SCHEMA.items():
        missing_columns = required - table_columns(connection, table)
        if missing_columns:
            raise DerivedStateValidationError(
                "CodeIntel derived-state schema is incompatible; rebuild required. "
                f"Table {table!r} is missing columns {sorted(missing_columns)}"
            )


def validate_production_schema(connection: apsw.Connection) -> None:
    version = int(connection.pragma("user_version") or 0)
    if version not in (0, CODEINTEL_DB_USER_VERSION):
        raise DerivedStateValidationError(
            f"Unsupported CodeIntel DB user_version {version}; use a compatible version or a new state directory")
    with fts_read_snapshot(connection):
        validate_legacy_schema(connection)
        if connection.execute("SELECT 1 FROM sqlite_schema WHERE name = 'generations'").fetchone() is None:
            return
        _validate_primary_keys(connection)
        _validate_fts_schema(connection)
        _validate_generation_foreign_keys(connection)


def _assert_single_active_generation(connection: apsw.Connection) -> None:
    row = connection.execute(
        "SELECT repo_id, COUNT(*) AS active_count "
        "FROM generations WHERE is_active = 1 "
        "GROUP BY repo_id HAVING COUNT(*) > 1 LIMIT 1"
    ).fetchone()
    if row is not None:
        repo_id = _row_col(row, 0, "repo_id")
        count = int(_row_col(row, 1, "active_count"))
        raise DerivedStateValidationError(
            "CodeIntel derived-state generations are corrupt; rebuild required. "
            f"Repository {repo_id!r} has {count} active generations"
        )


def _assert_database_integrity(connection: apsw.Connection) -> None:
    row = connection.execute("PRAGMA quick_check(1)").fetchone()
    if row is None or str(_row_col(row, 0)).lower() != "ok":
        detail = None if row is None else str(_row_col(row, 0))
        raise DerivedStateValidationError(
            "CodeIntel derived-state SQLite quick_check failed; rebuild required. "
            f"Result: {detail!r}"
        )
    fk_row = connection.execute("PRAGMA foreign_key_check").fetchone()
    if fk_row is not None:
        raise DerivedStateValidationError(
            "CodeIntel derived-state foreign-key integrity failed; rebuild required"
        )
    validate_fts_coherence(connection)


def validate_fts_coherence(connection: apsw.Connection, generation_id: str | None = None) -> None:
    """Compare each FTS corpus with canonical chunks in one pinned read snapshot."""
    with fts_read_snapshot(connection):
        ids = ([generation_id] if generation_id is not None else
               [str(_row_col(row, 0, "generation_id")) for row in connection.execute(
                   "SELECT generation_id FROM generations ORDER BY generation_id")])
        for current in ids:
            table = generation_fts_name(current)
            row = connection.execute(f"""
                SELECT
                  (SELECT COUNT(*) FROM chunks WHERE generation_id = ?)
                    <> (SELECT COUNT(*) FROM "{table}")
                  OR EXISTS (
                    SELECT 1 FROM "{table}" AS f LEFT JOIN chunks AS c
                      ON c.generation_id = f.generation_id AND c.chunk_id = f.chunk_id
                    WHERE f.generation_id IS NOT ? OR c.chunk_id IS NULL
                      OR f.file_id IS NOT c.file_id OR f.content IS NOT c.content
                  )
                  OR EXISTS (
                    SELECT 1 FROM "{table}" GROUP BY chunk_id HAVING COUNT(*) <> 1
                  ) AS inconsistent
                """, (current, current)).fetchone()
            if row is None or bool(_row_col(row, 0, "inconsistent")):
                raise DerivedStateValidationError("CodeIntel derived-state FTS/chunk coherence failed; rebuild required")


def configure_codeintel_connection(connection: apsw.Connection) -> None:
    """Validate first, then atomically stamp policy onto one owned APSW DB."""
    if not is_codeintel_db(connection):
        raise DerivedStateValidationError(
            "SQLiteStore must point at a marked CodeIntel db.sqlite state database"
        )

    app_id = int(connection.pragma("application_id") or 0)
    # user_version is a compatibility marker
    user_version = int(connection.pragma("user_version") or 0)
    if app_id not in (0, CODEINTEL_DB_APPLICATION_ID):
        raise DerivedStateValidationError(
            f"State DB application_id {app_id} is not CodeIntel; refusing to open"
        )
    if user_version not in (0, CODEINTEL_DB_USER_VERSION):
        raise DerivedStateValidationError(
            f"Unsupported CodeIntel DB user_version {user_version}; "
            f"expected {CODEINTEL_DB_USER_VERSION}; use a compatible version or a new state directory"
        )
    validate_production_schema(connection)
    _assert_single_active_generation(connection)
    _assert_database_integrity(connection)

    connection.set_busy_timeout(CODEINTEL_DB_BUSY_TIMEOUT_MS)
    connection.pragma("foreign_keys", True)
    journal_mode = str(connection.pragma("journal_mode", "WAL") or "").lower()
    if journal_mode != "wal":
        raise DerivedStateValidationError(f"CodeIntel state DB requires WAL journal mode, got {journal_mode!r}")
    connection.pragma("synchronous", 1)
    if int(connection.pragma("foreign_keys") or 0) != 1:
        raise DerivedStateValidationError("CodeIntel state DB requires foreign_keys=ON")

    connection.execute("SAVEPOINT codeintel_policy")
    try:
        if app_id == 0:
            connection.pragma("application_id", CODEINTEL_DB_APPLICATION_ID)
        if user_version == 0:
            connection.pragma("user_version", CODEINTEL_DB_USER_VERSION)
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_generations_one_active_per_repo "
            "ON generations(repo_id) WHERE is_active = 1"
        )
        connection.execute("RELEASE SAVEPOINT codeintel_policy")
    except BaseException:
        if connection.in_transaction:
            connection.execute("ROLLBACK TO SAVEPOINT codeintel_policy")
            connection.execute("RELEASE SAVEPOINT codeintel_policy")
        raise

    if int(connection.pragma("application_id") or 0) != CODEINTEL_DB_APPLICATION_ID:
        raise DerivedStateValidationError("Failed to persist canonical CodeIntel application_id")
    if int(connection.pragma("user_version") or 0) != CODEINTEL_DB_USER_VERSION:
        raise DerivedStateValidationError("Failed to persist canonical CodeIntel user_version")
    validate_legacy_schema(connection)
    _assert_single_active_generation(connection)
    # The full quick_check and foreign-key scan already ran before the
    # savepoint. The controlled policy stamp cannot introduce derived rows;
    # rechecking the whole database here makes every open scan it twice.


_is_codeintel_db = is_codeintel_db
_table_columns = table_columns
_validate_legacy_schema = validate_legacy_schema
_configure_codeintel_connection = configure_codeintel_connection
