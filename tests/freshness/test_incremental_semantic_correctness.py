"""Historical filename; regressions now require complete snapshot rebuilds.

These tests preserve relation correctness across edits, not an incremental
indexing implementation or a speed claim. Keeping node IDs avoids relabeling
the historical acceptance collections.
"""
import pytest
import os
import shutil
import tempfile
import time
from pathlib import Path
from codeintel.service import create_default_service, DomainService
from codeintel.core.models import RelationType

@pytest.fixture
def temp_repo():
    d = tempfile.mkdtemp(prefix="test_incr_repo_")
    # Create git repo structure
    os.system(f"git -C {d} init -q")
    os.system(f"git -C {d} config user.email 'test@test.com'")
    os.system(f"git -C {d} config user.name 'Test'")
    
    # Create callee file
    callee_code = """
export class ComplianceService {
  public logAuditEvent(event: { action: string }): void {
    console.log("Compliance audit:", event.action);
  }
  public generateReport(): string {
    return "Report 1.0";
  }
}

export class MonitoringService {
  public logAuditEvent(event: { action: string }): void {
    console.log("Monitoring audit:", event.action);
  }
}
"""
    # Create caller file
    caller_code = """
import { ComplianceService, MonitoringService } from './callee';

export class AuditManager {
  private compliance = new ComplianceService();
  private monitoring = new MonitoringService();

  public runAudit(): void {
    this.compliance.logAuditEvent({ action: "test" });
  }
}
"""
    (Path(d) / "callee.ts").write_text(callee_code)
    (Path(d) / "caller.ts").write_text(caller_code)
    os.system(f"git -C {d} add . && git -C {d} commit -q -m 'initial'")
    
    state_dir = tempfile.mkdtemp(prefix="test_incr_state_")
    yield d, state_dir
    
    shutil.rmtree(d, ignore_errors=True)
    shutil.rmtree(state_dir, ignore_errors=True)



def _stored_impact(service, qualified_name):
    """Inspect retained graph data directly; the old agent-facing impact API is retired."""
    generation = service.barrier.active_generation.generation_id
    name = qualified_name.rsplit('.', 1)[-1]
    entities = [entity for entity in service.graph_store.get_entities_by_name(name, generation_id=generation)
                if entity.qualified_name == qualified_name or entity.qualified_name.endswith('.' + qualified_name)]
    if not entities:
        return {"found": False, "upstream_callers": []}
    assert len(entities) == 1
    callers = service.graph_store.get_callers(
        entities[0].entity_id, max_depth=1, generation_id=generation,
        include_heuristic=True,
    )
    return {"found": True, "upstream_callers": [
        {"entity": entity.qualified_name, "trust": trust}
        for _depth, entity, trust in callers
    ]}

def test_cross_file_semantics_and_rebuild_correctness(temp_repo):
    repo_dir, state_dir = temp_repo
    
    # 1. Full initial offline index
    service = create_default_service(repo_dir, state_dir=state_dir)
    res1 = service.reindex()
    assert res1["status"] == "SUCCESS"
    assert res1["is_incremental"] is False
    
    # Verify initial CALLS relation from AuditManager.runAudit -> ComplianceService.logAuditEvent
    imp_initial = _stored_impact(service, "ComplianceService.logAuditEvent")
    assert imp_initial["found"] is True
    callers1 = [c.get("entity") or c.get("qualified_name") for c in imp_initial.get("upstream_callers", [])]
    assert any("AuditManager.runAudit" in str(c) for c in callers1)
    
    # Verify MonitoringService.logAuditEvent is isolated (0 callers)
    imp_mon_initial = _stored_impact(service, "MonitoringService.logAuditEvent")
    assert imp_mon_initial["found"] is True
    assert len(imp_mon_initial.get("upstream_callers", [])) == 0

    # Test A: caller.ts changed, callee.ts unchanged -> relation preserved across full-snapshot reindex
    caller_modified = """
import { ComplianceService, MonitoringService } from './callee';

export class AuditManager {
  private compliance = new ComplianceService();
  private monitoring = new MonitoringService();

  public runAudit(): void {
    // Comment added, relation still present
    this.compliance.logAuditEvent({ action: "test_modified" });
  }
}
"""
    (Path(repo_dir) / "caller.ts").write_text(caller_modified)
    res2 = service.reindex()
    assert res2["status"] == "SUCCESS"
    assert res2["is_incremental"] is False
    
    imp_after_mod = _stored_impact(service, "ComplianceService.logAuditEvent")
    assert imp_after_mod["found"] is True
    callers2 = [c.get("entity") or c.get("qualified_name") for c in imp_after_mod.get("upstream_callers", [])]
    assert any("AuditManager.runAudit" in str(c) for c in callers2), "Cross-file CALLS relation lost after incremental edit!"

    # Test B: changed caller introduces a NEW call to an unchanged callee (MonitoringService.logAuditEvent)
    caller_with_new_call = """
import { ComplianceService, MonitoringService } from './callee';

export class AuditManager {
  private compliance = new ComplianceService();
  private monitoring = new MonitoringService();

  public runAudit(): void {
    this.compliance.logAuditEvent({ action: "test" });
    this.monitoring.logAuditEvent({ action: "mon_test" });
  }
}
"""
    (Path(repo_dir) / "caller.ts").write_text(caller_with_new_call)
    res3 = service.reindex()
    assert res3["is_incremental"] is False
    
    imp_mon_after = _stored_impact(service, "MonitoringService.logAuditEvent")
    assert imp_mon_after["found"] is True
    callers3 = [c.get("entity") or c.get("qualified_name") for c in imp_mon_after.get("upstream_callers", [])]
    assert any("AuditManager.runAudit" in str(c) for c in callers3), "New cross-file call to unchanged callee not resolved!"

    # Test C: changed caller removes call -> old relation disappears
    caller_remove_call = """
import { ComplianceService, MonitoringService } from './callee';

export class AuditManager {
  private compliance = new ComplianceService();
  private monitoring = new MonitoringService();

  public runAudit(): void {
    console.log("No audits called");
  }
}
"""
    (Path(repo_dir) / "caller.ts").write_text(caller_remove_call)
    res4 = service.reindex()
    assert res4["is_incremental"] is False
    
    imp_comp_removed = _stored_impact(service, "ComplianceService.logAuditEvent")
    assert len(imp_comp_removed.get("upstream_callers", [])) == 0, "Removed call relation still present!"

    # Test E: target method renamed/deleted in callee -> no dangling relation survives
    caller_restore_call = """
import { ComplianceService, MonitoringService } from './callee';

export class AuditManager {
  private compliance = new ComplianceService();
  public runAudit(): void {
    this.compliance.logAuditEvent({ action: "test" });
  }
}
"""
    (Path(repo_dir) / "caller.ts").write_text(caller_restore_call)
    service.reindex()
    
    # Rename method in callee.ts
    callee_renamed = """
export class ComplianceService {
  public auditLogRenamed(event: { action: string }): void {
    console.log("Renamed:", event.action);
  }
}
"""
    (Path(repo_dir) / "callee.ts").write_text(callee_renamed)
    res5 = service.reindex()
    assert res5["is_incremental"] is False
    
    # Verify no dangling relation pointing to old ComplianceService.logAuditEvent
    imp_old = _stored_impact(service, "ComplianceService.logAuditEvent")
    assert imp_old.get("found") is False or len(imp_old.get("upstream_callers", [])) == 0


def test_cold_restart_full_rebuild_detection(temp_repo):
    repo_dir, state_dir = temp_repo
    
    # 1. Initial build in Service 1
    service1 = create_default_service(repo_dir, state_dir=state_dir)
    res1 = service1.reindex()
    assert res1["status"] == "SUCCESS"
    assert res1["is_incremental"] is False
    
    # Destroy Service 1 object / process simulation
    service1.close()
    del service1
    
    # 2. Start new Service 2 on the same state_dir
    service2 = create_default_service(repo_dir, state_dir=state_dir)
    
    # Modify 1 file
    (Path(repo_dir) / "caller.ts").write_text("// cold restart mutation\n" + (Path(repo_dir) / "caller.ts").read_text())
    
    # 3. Reindex and assert snapshot-change detection succeeded
    res2 = service2.reindex()
    assert res2["status"] == "SUCCESS"
    assert res2["is_incremental"] is False, "The offline route must rebuild a complete snapshot"




def test_mutation_lifecycle_matrix(temp_repo):
    repo_dir, state_dir = temp_repo
    service = create_default_service(repo_dir, state_dir=state_dir)
    service.reindex()
    
    # No-op reindex
    noop_res = service.reindex()
    assert noop_res["status"] == "REUSED_EXISTING"
    
    # File Add
    (Path(repo_dir) / "helper.ts").write_text("export function helpMe(): string { return 'help'; }")
    add_res = service.reindex()
    assert add_res["is_incremental"] is False
    
    # File Delete
    os.remove(Path(repo_dir) / "helper.ts")
    del_res = service.reindex()
    assert del_res["is_incremental"] is False
    
    # Rapid consecutive edits
    for i in range(3):
        (Path(repo_dir) / "callee.ts").write_text(f"// rapid {i}\n" + (Path(repo_dir) / "callee.ts").read_text())
        r = service.reindex()
        assert r["is_incremental"] is False


def test_rebuild_fts_preservation_and_exact_match_invariant():
    """
    Deterministic regression test for Defect 1:
    Verifies that full-snapshot reindexing does NOT destroy FTS rows for unchanged files.
    Asserts set(chunks_fts chunk_ids) == set(chunks chunk_ids) across all mutation types:
    edit, add, delete, rename, revert, multi-file, and cold restart.
    """
    with tempfile.TemporaryDirectory(prefix="test_fts_repo_") as repo_dir, \
         tempfile.TemporaryDirectory(prefix="test_fts_state_") as state_dir:
        
        os.system(f"git -C {repo_dir} init -q")
        os.system(f"git -C {repo_dir} config user.email 'test@test.com'")
        os.system(f"git -C {repo_dir} config user.name 'Test'")
        
        file_a = Path(repo_dir) / "A.ts"
        file_b = Path(repo_dir) / "B.ts"
        file_a.write_text("export function processPayment() { return 'payment_signature_alpha'; }")
        file_b.write_text("export function verifyIdentity() { return 'unique_biometric_auth_token'; }")
        os.system(f"git -C {repo_dir} add . && git -C {repo_dir} commit -q -m 'init'")
        
        # 1. Full Initial Index
        service = create_default_service(repo_dir, state_dir=state_dir)
        r1 = service.reindex()
        gen1 = r1["generation_id"]
        
        # Verify initial lexical search finds content in unchanged B.ts
        hits1 = service.search("unique_biometric_auth_token")
        assert len(hits1) > 0
        assert "B.ts" in hits1[0]["path"]
        
        # Invariant check
        db_cids1 = set(service.graph_store.get_all_chunk_ids(gen1))
        fts_cids1 = set(service.graph_store.get_all_fts_chunk_ids(gen1))
        assert db_cids1 == fts_cids1
        assert len(db_cids1) >= 2

        # 2. Modify ONLY A.ts
        file_a.write_text("export function processPayment() { return 'payment_signature_beta_v2'; }")
        r2 = service.reindex()
        assert r2["is_incremental"] is False
        gen2 = r2["generation_id"]
        
        # CRITICAL ASSERTION: Lexical query for unchanged B.ts MUST STILL SUCCEED!
        hits2_b = service.search("unique_biometric_auth_token")
        assert len(hits2_b) > 0, "Regression: Full-snapshot reindex destroyed FTS rows for unchanged B.ts!"
        assert "B.ts" in hits2_b[0]["path"]
        
        # Query for changed A.ts succeeds
        hits2_a = service.search("payment_signature_beta_v2")
        assert len(hits2_a) > 0
        assert "A.ts" in hits2_a[0]["path"]
        
        # Invariant check after edit
        db_cids2 = set(service.graph_store.get_all_chunk_ids(gen2))
        fts_cids2 = set(service.graph_store.get_all_fts_chunk_ids(gen2))
        assert db_cids2 == fts_cids2, f"FTS chunk IDs mismatch DB chunk IDs: {len(fts_cids2)} != {len(db_cids2)}"

        # 3. Add file C.ts
        file_c = Path(repo_dir) / "C.ts"
        file_c.write_text("export function auditLog() { return 'immutable_audit_entry_gamma'; }")
        r3 = service.reindex()
        assert r3["is_incremental"] is False
        gen3 = r3["generation_id"]
        
        hits3_c = service.search("immutable_audit_entry_gamma")
        assert len(hits3_c) > 0
        assert "C.ts" in hits3_c[0]["path"]
        assert len(service.search("unique_biometric_auth_token")) > 0
        assert set(service.graph_store.get_all_chunk_ids(gen3)) == set(service.graph_store.get_all_fts_chunk_ids(gen3))

        # 4. Delete file C.ts
        os.remove(file_c)
        r4 = service.reindex()
        assert r4["is_incremental"] is False
        gen4 = r4["generation_id"]
        assert len(service.search("immutable_audit_entry_gamma")) == 0
        assert len(service.search("unique_biometric_auth_token")) > 0
        assert set(service.graph_store.get_all_chunk_ids(gen4)) == set(service.graph_store.get_all_fts_chunk_ids(gen4))

        # 5. Rename B.ts -> B_renamed.ts
        file_b_renamed = Path(repo_dir) / "B_renamed.ts"
        file_b.rename(file_b_renamed)
        r5 = service.reindex()
        assert r5["is_incremental"] is False
        gen5 = r5["generation_id"]
        hits5 = service.search("unique_biometric_auth_token")
        assert len(hits5) > 0
        assert "B_renamed.ts" in hits5[0]["path"]
        assert set(service.graph_store.get_all_chunk_ids(gen5)) == set(service.graph_store.get_all_fts_chunk_ids(gen5))

        # 6. Revert A.ts
        file_a.write_text("export function processPayment() { return 'payment_signature_alpha'; }")
        r6 = service.reindex()
        assert r6["is_incremental"] is False
        gen6 = r6["generation_id"]
        assert len(service.search("payment_signature_alpha")) > 0
        assert len(service.search("payment_signature_beta_v2")) == 0
        assert set(service.graph_store.get_all_chunk_ids(gen6)) == set(service.graph_store.get_all_fts_chunk_ids(gen6))
        service.close()
