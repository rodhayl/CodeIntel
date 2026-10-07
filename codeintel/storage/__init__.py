"""Production storage surface with explicit CodeIntel-owned APSW policy."""

from codeintel.storage.base import GenerationStore, GraphStore, LexicalIndex
from codeintel.storage.policy import (
    CODEINTEL_DB_APPLICATION_ID,
    CODEINTEL_DB_BUSY_TIMEOUT_MS,
    CODEINTEL_DB_USER_VERSION,
    _configure_codeintel_connection,
    _is_codeintel_db,
    _table_columns,
    _validate_legacy_schema,
    configure_codeintel_connection,
    is_codeintel_db,
    table_columns,
    validate_legacy_schema,
)
from codeintel.storage.production import ProductionSQLiteStore

# Public production alias. The unconfigured implementation remains available
# as codeintel.storage.sqlite_store.SQLiteStore for low-level/internal tests.
SQLiteStore = ProductionSQLiteStore

__all__ = [
    "GraphStore",
    "LexicalIndex",
    "GenerationStore",
    "SQLiteStore",
    "ProductionSQLiteStore",
    "CODEINTEL_DB_APPLICATION_ID",
    "CODEINTEL_DB_USER_VERSION",
    "CODEINTEL_DB_BUSY_TIMEOUT_MS",
    "configure_codeintel_connection",
    "validate_legacy_schema",
    "is_codeintel_db",
    "table_columns",
]
