import hashlib
from pathlib import Path

import pytest

from codeintel.core.identity import canonical_hash
from codeintel.core.models import (
    Chunk,
    Entity,
    EntityKind,
    Generation,
    Relation,
    RelationType,
    TrustClass,
)
from codeintel.core.security import SecurityException, get_trusted_state_dir
from codeintel.freshness.hardened import HardenedProductionGenerationBarrier as GenerationBarrier
from codeintel.parsing.chunking import MAX_INDEX_CHUNK_BYTES, split_chunk
from codeintel.parsing.relation_resolver import resolve_syntactic_call


class _GenStore:
    def __init__(self, active=None):
        self.active = active
        self.generations = [active] if active else []
        self.metadata_updates = []
        self.sequence = 0

    def get_active_generation(self, repo_id):
        return self.active

    def list_generations(self, repo_id):
        return [g for g in self.generations if g is not None]

    def create_generation(self, repo_id, sequence, snapshot_hash, build_fingerprint=None, metadata=None):
        self.sequence = sequence
        gen = Generation(
            generation_id=f"gen_{sequence}",
            repo_id=repo_id,
            sequence=sequence,
            snapshot_hash=snapshot_hash,
            is_active=False,
            metadata=dict(metadata or {}),
        )
        self.generations.append(gen)
        return gen

    def activate_generation(self, generation_id):
        for gen in self.generations:
            gen.is_active = gen.generation_id == generation_id
            if gen.is_active:
                self.active = gen

    def update_generation_metadata(self, generation_id, metadata):
        self.metadata_updates.append((generation_id, dict(metadata)))
        for gen in self.generations:
            if gen.generation_id == generation_id:
                gen.metadata.update(metadata)

    def delete_generation_data(self, generation_id):
        self.generations = [g for g in self.generations if g.generation_id != generation_id]


def _snapshot_hash(paths):
    hashes = {
        name: hashlib.sha256(content.encode("utf-8")).hexdigest()
        for name, content in paths.items()
    }
    digest = canonical_hash("\n".join(sorted(f"{k}:{v}" for k, v in hashes.items())))
    return hashes, digest


def test_missing_build_fingerprint_is_never_treated_as_fresh(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    hashes, snapshot = _snapshot_hash({"a.py": "x = 1\n"})
    gen = Generation("g1", "repo", 1, snapshot, True, metadata={})
    store = _GenStore(gen)
    monkeypatch.setenv("CODEINTEL_STATE_DIR", str(tmp_path / "state"))
    barrier = GenerationBarrier("repo", str(repo), store, build_fingerprint="new-fp")
    barrier.file_hashes = hashes
    fresh, *_ = barrier.check_freshness()
    assert fresh is False


def test_staging_activation_refuses_worktree_changed_after_index(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "a.py"
    target.write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setenv("CODEINTEL_STATE_DIR", str(tmp_path / "state"))
    store = _GenStore()
    barrier = GenerationBarrier("repo", str(repo), store, build_fingerprint="fp")
    staging = barrier.create_staging_generation()
    indexed_hashes = dict(staging.current_hashes)
    target.write_text("x = 2\n", encoding="utf-8")
    with pytest.raises(Exception, match="changed while staging"):
        barrier.commit_staging_generation(staging.generation, indexed_hashes)
    assert store.active is None


def test_staging_generation_is_full_rebuild_until_incremental_invalidation_is_proven(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setenv("CODEINTEL_STATE_DIR", str(tmp_path / "state"))
    store = _GenStore()
    barrier = GenerationBarrier("repo", str(repo), store, build_fingerprint="fp")
    staging = barrier.create_staging_generation()
    assert not hasattr(staging, "prior_generation")
    assert not hasattr(staging, "prior_hashes")
    assert staging.generation.metadata["indexing_mode"] == "FULL_REBUILD_V1"
    barrier.abort_staging_generation(staging.generation)


def test_trusted_state_rejects_symlink_leaf(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    root = tmp_path / "state"
    monkeypatch.setenv("CODEINTEL_STATE_DIR", str(root))
    canonical = str(repo.resolve())
    repo_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    leaf = root / "repos" / f"{repo.name}_{repo_hash}"
    leaf.parent.mkdir(parents=True)
    attacker = tmp_path / "attacker"
    attacker.mkdir()
    leaf.symlink_to(attacker, target_is_directory=True)
    with pytest.raises(SecurityException, match="symlink"):
        get_trusted_state_dir(str(repo))


def test_large_utf8_chunk_is_losslessly_bounded():
    text = ("π = 'é'\n" * 500) + ("界" * 800)
    chunk = Chunk(
        chunk_id="old",
        file_id="file_a",
        generation_id="gen_1",
        rel_path="src/a.py",
        span=(1, 0, 501, 800),
        content=text,
        content_hash=canonical_hash(text),
        entity_ids=["ent_a"],
    )
    parts = split_chunk(chunk)
    assert len(parts) > 1
    assert all(len(part.content.encode("utf-8")) <= MAX_INDEX_CHUNK_BYTES for part in parts)
    assert "".join(part.content for part in parts) == text
    assert all(part.entity_ids == ["ent_a"] for part in parts)


def _entity(eid, name, kind, file_id, parent_id=None):
    return Entity(
        entity_id=eid,
        repo_id="repo",
        file_id=file_id,
        generation_id="gen",
        name=name,
        qualified_name=name,
        kind=kind,
        span=(1, 0, 2, 0),
        signature=name,
        properties={"parent_id": parent_id} if parent_id else {},
    )


def test_receiver_naming_convention_never_becomes_authoritative_resolved_edge():
    service = _entity("cls", "ComplianceService", EntityKind.CLASS, "service.py")
    method = _entity("method", "check", EntityKind.METHOD, "service.py", "cls")
    caller = _entity("caller", "run", EntityKind.FUNCTION, "caller.py")
    relation = Relation(
        relation_id="raw",
        repo_id="repo",
        generation_id="gen",
        source_id="caller",
        target_id="unresolved",
        rel_type=RelationType.CALLS,
        file_id="caller.py",
        span=(1, 0, 1, 10),
        trust_class=TrustClass.EXACT,
        properties={"callee_name": "check", "receiver_name": "compliance"},
    )
    result = resolve_syntactic_call(relation, [service, method, caller])
    assert result
    assert all(item.trust_class == TrustClass.HEURISTIC for item in result)


def test_explicit_unique_receiver_type_is_resolved():
    service = _entity("cls", "ComplianceService", EntityKind.CLASS, "service.py")
    method = _entity("method", "check", EntityKind.METHOD, "service.py", "cls")
    caller = _entity("caller", "run", EntityKind.FUNCTION, "caller.py")
    relation = Relation(
        relation_id="raw",
        repo_id="repo",
        generation_id="gen",
        source_id="caller",
        target_id="unresolved",
        rel_type=RelationType.CALLS,
        file_id="caller.py",
        span=(1, 0, 1, 10),
        trust_class=TrustClass.EXACT,
        properties={
            "callee_name": "check",
            "receiver_name": "svc",
            "receiver_type": "ComplianceService",
        },
    )
    result = resolve_syntactic_call(relation, [service, method, caller])
    assert len(result) == 1
    assert result[0].entity.entity_id == "method"
    assert result[0].trust_class == TrustClass.RESOLVED
