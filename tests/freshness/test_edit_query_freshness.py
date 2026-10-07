import pytest
import os
import tempfile
import shutil
from pathlib import Path
from codeintel.service import create_default_service

def test_edit_and_mutation_freshness():
    """Verify that file mutations (add, edit, delete) reflect in index after reindex without stale results."""
    tmp_dir = tempfile.mkdtemp(prefix="codeintel_freshness_test_")
    repo_dir = os.path.join(tmp_dir, "repo")
    state_dir = os.path.join(tmp_dir, "state")
    os.makedirs(repo_dir, exist_ok=True)
    
    try:
        # 1. Initial file
        test_file = os.path.join(repo_dir, "service.ts")
        with open(test_file, "w") as f:
            f.write("export function calculateTax(amount: number): number { return amount * 0.2; }\n")
            
        # This checks syntax-index freshness, not an installed SCIP toolchain.
        service = create_default_service(repo_dir, state_dir=state_dir)
        service.reindex()
        
        sym1 = {"found": bool(service.graph_store.get_entities_by_name("calculateTax", generation_id=service.barrier.active_generation.generation_id))}
        assert sym1.get("found") is True
        
        # 2. Edit file: rename method to calculateVat
        with open(test_file, "w") as f:
            f.write("export function calculateVat(amount: number): number { return amount * 0.2; }\n")
            
        service.reindex()
        
        # calculateTax must no longer be found
        sym_old = {"found": bool(service.graph_store.get_entities_by_name("calculateTax", generation_id=service.barrier.active_generation.generation_id))}
        assert sym_old.get("found") is False, "Stale symbol calculateTax returned after edit"
        
        # calculateVat must be found
        sym_new = {"found": bool(service.graph_store.get_entities_by_name("calculateVat", generation_id=service.barrier.active_generation.generation_id))}
        assert sym_new.get("found") is True
        
        # 3. Delete file
        os.remove(test_file)
        service.reindex()
        
        sym_deleted = {"found": bool(service.graph_store.get_entities_by_name("calculateVat", generation_id=service.barrier.active_generation.generation_id))}
        assert sym_deleted.get("found") is False, "Stale symbol calculateVat returned after file deletion"
        
    finally:
        if "service" in locals():
            service.close()
        shutil.rmtree(tmp_dir)
