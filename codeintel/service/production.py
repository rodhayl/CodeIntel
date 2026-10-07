"""Owned-resource offline service over the existing generation-bound engine.

The historical module/class names are retained for imports, not production claims.
"""
import os
import threading
from typing import List, Optional
from codeintel.core.identity import make_repo_id
from codeintel.core.security import get_trusted_state_dir, validate_trusted_state_dir
from codeintel.freshness.barrier import compute_index_build_fingerprint
from codeintel.freshness.hardened import HardenedProductionGenerationBarrier
from codeintel.parsing.semantic_merger import SemanticMerger
from codeintel.retrieval.hybrid import HybridRetriever
from codeintel.service.domain_service import DomainService as BaseDomainService
from codeintel.storage.production import ProductionSQLiteStore

class ProductionDomainService(BaseDomainService):
    def __init__(self, repo_root, graph_store, lexical_index, generation_store,
                 state_dir=None, owns_storage=False):
        if not isinstance(owns_storage, bool):
            raise ValueError("owns_storage must be boolean")
        self.repo_root = os.path.abspath(repo_root)
        self.repo_id = make_repo_id(self.repo_root)
        self.state_dir = (validate_trusted_state_dir(self.repo_root, state_dir)
                          if state_dir is not None else get_trusted_state_dir(self.repo_root))
        self.graph_store, self.lexical_index, self.generation_store = graph_store, lexical_index, generation_store
        self._owns_storage, self._closed = owns_storage, False
        self.merger = SemanticMerger()
        self._generation_lock = threading.RLock()
        self.build_fingerprint = compute_index_build_fingerprint()
        self.barrier = HardenedProductionGenerationBarrier(
            self.repo_id, self.repo_root, self.generation_store,
            state_dir=self.state_dir, build_fingerprint=self.build_fingerprint)
        try:
            self._semantic_status = "SYNTAX_ONLY"
            self._scip_failure_codes = []
            active = self.barrier.active_generation
            if active:
                self._adopt_active_generation(active)
                hashes = self.graph_store.get_file_hashes(active.generation_id)
                if hashes:
                    self.barrier.file_hashes = hashes
            self.retriever = HybridRetriever(self.graph_store, self.lexical_index)
            self._test_mid_request_hook = None
        except BaseException as error:
            self._closed = True
            self._barrier_closed = True
            try:
                self.barrier.close()
            except BaseException as cleanup_error:
                error.add_note(f"Offline service cleanup also failed: {cleanup_error}")
            raise

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        errors: List[BaseException] = []
        if not getattr(self, "_barrier_closed", False):
            try:
                self.barrier.close()
                self._barrier_closed = True
            except BaseException as exc:
                errors.append(exc)
        if self._owns_storage:
            if not hasattr(self, "_closed_stores"):
                self._closed_stores = set()
            seen = set()
            for store in (self.graph_store, self.lexical_index, self.generation_store):
                if id(store) in seen or id(store) in self._closed_stores:
                    continue
                seen.add(id(store))
                close = getattr(store, "close", None)
                if callable(close):
                    try:
                        close()
                        self._closed_stores.add(id(store))
                    except BaseException as exc:
                        errors.append(exc)
        if errors:
            fatal = next((exc for exc in errors if not isinstance(exc, Exception)), None)
            if fatal is not None:
                for secondary in errors:
                    if secondary is not fatal:
                        fatal.add_note(
                            "ProductionDomainService close also failed: "
                            f"{type(secondary).__name__}: {secondary}"
                        )
                raise fatal
            raise RuntimeError(
                "ProductionDomainService close failed: "
                + "; ".join(f"{type(exc).__name__}: {exc}" for exc in errors)
            )
        self._closed = True


    def __del__(self):
        try:
            self.close()
        except BaseException:
            pass


def create_default_service(repo_root: str, state_dir: Optional[str] = None,
                           enable_dense: bool = False, enable_scip: bool = False):
    # Retired capability requests fail before state creation or source inspection.
    if not isinstance(enable_dense, bool) or not isinstance(enable_scip, bool):
        raise ValueError("enable_dense and enable_scip must be boolean")
    if enable_dense or enable_scip:
        raise ValueError("Dense providers and SCIP are not part of the offline distribution")
    root = os.path.abspath(repo_root)
    state = validate_trusted_state_dir(root, state_dir) if state_dir is not None else get_trusted_state_dir(root)
    store = ProductionSQLiteStore(os.path.join(state, "db.sqlite"))
    try:
        return ProductionDomainService(root, store, store, store, state_dir=state, owns_storage=True)
    except BaseException as error:
        try:
            store.close()
        except BaseException as cleanup_error:
            error.add_note(f"Offline factory cleanup also failed: {cleanup_error}")
        raise
