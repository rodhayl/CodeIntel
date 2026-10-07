"""Shared limits for offline indexing, storage and literal retrieval."""

# Bump whenever canonical parser/chunk ownership semantics change. v6 also
# drops typed receiver evidence for transparent writes and generic shadows,
# and excludes nested abstract-class bodies from outer call ownership.
# Previous generations must be rebuilt, including unchanged source snapshots.
INDEX_CHUNK_POLICY_VERSION = "utf8-entity-partition-v6-typed-scope-provenance"
# Snapshot paths may contain colons/newlines. Frame path/hash pairs explicitly;
# changing this format also changes the persisted index build identity.
INDEX_SNAPSHOT_FORMAT_VERSION = "codeintel-snapshot-v2-json-pairs"
MAX_INDEX_CHUNK_BYTES = 1800
# Production indexing reads/parses whole source files before chunking; bound that
# pre-chunk surface so an untrusted checkout cannot force unbounded memory use.
MAX_INDEX_SOURCE_FILE_BYTES = 16 * 1024 * 1024
# Bound candidate enumeration independently from per-file reads. Git output is captured
# only to this size and repositories above the file-count contract fail closed.
MAX_INDEX_CANDIDATE_FILES = 100_000
# Count admitted child directories separately, including empty directories.
MAX_INDEX_CANDIDATE_DIRECTORIES = 100_000
MAX_GIT_CANDIDATE_OUTPUT_BYTES = 32 * 1024 * 1024
# Ignore configuration participates in snapshot identity before indexing. Bound each
# file separately so effective repository ignore configuration stays bounded.
MAX_IGNORE_FINGERPRINT_FILE_BYTES = 1024 * 1024
# Bound direct lexical storage queries independently from the lab packet limit.
MAX_TASK_QUERY_BYTES = 32768

# Keep the active generation plus the two newest predecessors; leased generations are never deleted.
GENERATION_RETENTION_COUNT = 3
