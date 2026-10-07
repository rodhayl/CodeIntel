"""Full-snapshot index and generation-consistent lexical read operations.

The offline service reuses the existing source snapshots, parsing, FTS consistency gates and atomic activation. Model/context/GUI APIs are retired.
"""
import time
from typing import Any, Callable, Dict, List
from codeintel.core.models import FileRecord
from codeintel.core.identity import make_file_id
from codeintel.core.security import FreshnessBusyError
from codeintel.freshness.barrier import StagingResult
from codeintel.freshness.snapshot_binding import read_generation_source_bytes
from codeintel.parsing.scip_diagnostics import validate_scip_failure_codes
from codeintel.storage.policy import DerivedStateValidationError

class DomainService:
    """Internal operations shared with the owned-resource offline service."""
    def _adopt_active_generation(self, active_gen) -> None:
        with self._generation_lock:
            metadata = getattr(active_gen, "metadata", {}) or {}
            try:
                failure_codes = validate_scip_failure_codes(metadata.get("scip_failure_codes"))
            except ValueError as exc:
                raise DerivedStateValidationError(
                    "Stored SCIP failure diagnostics are invalid; rebuild required"
                ) from exc
            self._semantic_status = metadata.get("semantic_status", "SYNTAX_ONLY")
            self._scip_failure_codes = failure_codes

    def reindex(self) -> Dict[str, Any]:
        """Performs a complete source scan, parse, FTS build and atomic generation activation."""
        t0 = time.time()
        staging_res: StagingResult = self.barrier.create_staging_generation()
        staging_gen = staging_res.generation

        # If another process or previous build already committed this exact state, adopt idempotently
        if staging_res.reused_existing:
            self._adopt_active_generation(staging_gen)
            return {
                "status": "REUSED_EXISTING",
                "generation_id": staging_gen.generation_id,
                "files_count": len(staging_res.current_hashes),
                "semantic_status": self._semantic_status,
                "scip_failure_codes": list(self._scip_failure_codes),
                "elapsed_seconds": round(time.time() - t0, 3)
            }

        try:
            rel_paths = list(staging_res.current_hashes.keys())
            status = "SYNTAX_ONLY"
            entities_count = 0
            chunks_count = 0


            files: List[FileRecord] = []
            for rel_path, c_hash in staging_res.current_hashes.items():
                size_bytes = len(read_generation_source_bytes(
                    self.repo_root, rel_path, staging_gen.generation_id
                ))
                file_id = make_file_id(self.repo_id, rel_path)
                files.append(FileRecord(
                    file_id=file_id,
                    repo_id=self.repo_id,
                    generation_id=staging_gen.generation_id,
                    rel_path=rel_path,
                    content_hash=c_hash,
                    size_bytes=size_bytes
                ))
            if hasattr(self.graph_store, "add_files"):
                self.graph_store.add_files(files)
            entities, relations, chunks, status = self.merger.parse_repository(
                repo_id=self.repo_id,
                repo_root=self.repo_root,
                generation_id=staging_gen.generation_id,
                file_paths=rel_paths
            )
            self.graph_store.add_entities(entities)
            self.graph_store.add_relations(relations)
            self.lexical_index.index_chunks(chunks, is_full_replacement=True)
            if hasattr(self.graph_store, "get_all_chunk_ids") and hasattr(self.graph_store, "get_all_fts_chunk_ids"):
                db_cids = set(self.graph_store.get_all_chunk_ids(staging_gen.generation_id))
                fts_cids = set(self.graph_store.get_all_fts_chunk_ids(staging_gen.generation_id))
                if db_cids != fts_cids:
                    missing_fts = db_cids - fts_cids
                    extra_fts = fts_cids - db_cids
                    raise RuntimeError(f"FTS invariant violation: missing {len(missing_fts)} in FTS, extra {len(extra_fts)} in FTS for gen {staging_gen.generation_id}")
            entities_count = len(entities)
            chunks_count = len(chunks)

            # Atomic activation with per-process generation lock
            with self._generation_lock:
                if hasattr(self.generation_store, "update_generation_metadata"):
                    self.generation_store.update_generation_metadata(
                        staging_gen.generation_id,
                        {
                            "semantic_status": status,
                            "scip_failure_codes": [],
                            "build_fingerprint": self.build_fingerprint,
                        }
                    )
                self.barrier.commit_staging_generation(staging_gen, staging_res.current_hashes)
                self._semantic_status = status
                self._scip_failure_codes = []

            elapsed = time.time() - t0
            res_dict = {
                "status": "SUCCESS",
                "generation_id": staging_gen.generation_id,
                "files_count": len(rel_paths),
                "entities_count": entities_count,
                "chunks_count": chunks_count,
                "semantic_status": status,
                "scip_failure_codes": [],
                "is_incremental": False,
                "elapsed_seconds": round(elapsed, 3)
            }
            return res_dict
        except BaseException:
            self.barrier.abort_staging_generation(staging_gen)
            raise
        finally:
            self.barrier.release_rebuild_lock()


    def ensure_fresh(self) -> None:
        fresh, *_ = self.barrier.check_freshness()
        if not fresh:
            self.reindex()
        elif self.barrier.active_generation:
            self._adopt_active_generation(self.barrier.active_generation)

    def _execute_generation_consistent(self, op_fn: Callable[[str], Any]) -> Any:
        """
        Executes a read operation with generation pinning, atomic process view,
        and pre-return revalidation.
        """
        max_retries = 3
        for attempt in range(max_retries):
            self.ensure_fresh()
            with self._generation_lock:
                active_gen = self.barrier.active_generation
                gen_id = active_gen.generation_id if active_gen else "gen_000000_uninit"
                result = op_fn(gen_id)

            if self._test_mid_request_hook and callable(self._test_mid_request_hook):
                self._test_mid_request_hook()

            is_fresh, _, _, _ = self.barrier.check_freshness()
            current_active = self.barrier.active_generation
            current_gen_id = current_active.generation_id if current_active else None

            if is_fresh and current_gen_id == gen_id:
                return result

        raise FreshnessBusyError("Repository under continuous mutation; could not establish stable generation within retry budget.")


    def search(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Return complete lexical/syntax evidence; the lab owns packet serialization."""
        if not isinstance(query, str) or not query.strip():
            raise ValueError("search query must be non-empty")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError("search limit must be between 1 and 100")
        def operation(generation_id):
            return [row.to_dict() for row in self.retriever.retrieve(
                query, generation_id, limit=limit)]
        return self._execute_generation_consistent(operation)
