from dataclasses import replace
from typing import Dict, List, Set, Tuple

from codeintel.core.identity import make_file_id, make_relation_id
from codeintel.core.models import Entity, EntityKind, Relation, RelationType, TrustClass, Chunk
from codeintel.freshness.snapshot_binding import read_generation_source_text
from codeintel.parsing.chunking import bound_chunks
from codeintel.parsing.relation_resolver import CallResolutionIndex, resolve_syntactic_call
from codeintel.parsing.parser_dispatch import CanonicalTreeSitterCodeParser


class SemanticMerger:
    def __init__(self):
        self.ts_parser = CanonicalTreeSitterCodeParser()

    @staticmethod
    def _literal_file_span(content: str) -> Tuple[int, int, int, int]:
        """Tree-sitter-style end-exclusive UTF-8 byte span for a whole source file."""
        if not isinstance(content, str):
            raise TypeError("source content must be text")
        raw = content.encode("utf-8")
        newline_count = raw.count(b"\n")
        if newline_count == 0:
            return (1, 0, 1, len(raw))
        return (1, 0, 1 + newline_count, len(raw.rsplit(b"\n", 1)[1]))

    @classmethod
    def _canonicalize_module_entities(
        cls, entities: List[Entity], content: str
    ) -> List[Entity]:
        """Bind MODULE entity provenance to the same literal bytes used for parsing."""
        file_span = cls._literal_file_span(content)
        return [
            replace(entity, span=file_span)
            if entity.kind == EntityKind.MODULE and tuple(entity.span) != file_span
            else entity
            for entity in entities
        ]

    @staticmethod
    def _unique_entity_index(entities: List[Entity]) -> Dict[str, Entity]:
        """Return one deterministic entity per ID or reject conflicting provenance."""
        by_id: Dict[str, Entity] = {}
        for entity in entities:
            prior = by_id.get(entity.entity_id)
            if prior is not None and prior != entity:
                raise ValueError(
                    f"conflicting duplicate entity_id in semantic universe: {entity.entity_id}"
                )
            by_id[entity.entity_id] = entity
        return by_id

    def parse_repository(
        self,
        repo_id: str,
        repo_root: str,
        generation_id: str,
        file_paths: List[str],
    ) -> Tuple[List[Entity], List[Relation], List[Chunk], str]:
        """Parse a complete snapshot into syntax candidates and bounded literal chunks."""
        all_entities: List[Entity] = []
        all_relations: List[Relation] = []
        all_chunks: List[Chunk] = []

        # 1. Tree-sitter is the canonical identity/source substrate. Production STAGING
        # generations are read only through the exact path->SHA256 snapshot registered by
        # HardenedProductionGenerationBarrier. Direct/non-production merger use remains
        # supported, but still gets descriptor-safe bounded source reads.
        for rel_path in file_paths:
            content = read_generation_source_text(repo_root, rel_path, generation_id)
            file_id = make_file_id(repo_id, rel_path)
            entities, relations, chunks = self.ts_parser.parse_file(
                repo_id=repo_id,
                file_id=file_id,
                generation_id=generation_id,
                rel_path=rel_path,
                content=content,
            )
            entities = self._canonicalize_module_entities(entities, content)
            all_entities.extend(entities)
            all_relations.extend(relations)
            all_chunks.extend(chunks)

        entity_by_id = self._unique_entity_index(all_entities)
        combined_entities = list(entity_by_id.values())
        entity_id_set: Set[str] = set(entity_by_id)

        # Structural syntax relations only; no external compiler/indexer execution.
        resolved_relations_by_id: Dict[str, Relation] = {}

        # 3. Resolve syntax CALL observations with explicit trust classes.
        base_relations = list(all_relations)
        call_resolution_index = CallResolutionIndex.build(combined_entities)
        for relation in base_relations:
            if relation.rel_type != RelationType.CALLS or relation.trust_class != TrustClass.EXACT:
                continue
            for resolved in resolve_syntactic_call(relation, call_resolution_index):
                target = resolved.entity
                if target.entity_id == relation.target_id:
                    continue
                if relation.source_id not in entity_id_set or target.entity_id not in entity_id_set:
                    continue
                relation_id = make_relation_id(
                    relation.source_id,
                    RelationType.CALLS.value,
                    target.entity_id,
                    relation.file_id,
                    relation.span,
                )
                candidate = Relation(
                    relation_id=relation_id,
                    repo_id=repo_id,
                    generation_id=generation_id,
                    source_id=relation.source_id,
                    target_id=target.entity_id,
                    rel_type=RelationType.CALLS,
                    file_id=relation.file_id,
                    span=relation.span,
                    trust_class=resolved.trust_class,
                    properties={
                        "resolved_by": "STRUCTURAL_SYNTAX" if resolved.trust_class == TrustClass.RESOLVED else "NAMING_HEURISTIC",
                        "reason": resolved.reason,
                        "callee_name": relation.properties.get("callee_name"),
                        "receiver_name": relation.properties.get("receiver_name"),
                        "receiver_type": relation.properties.get("receiver_type"),
                    },
                )
                # Separate call sites carry separate literal provenance and may
                # have different evidence strengths for the same endpoint pair.
                prior = resolved_relations_by_id.get(relation_id)
                if prior is not None:
                    if prior != candidate:
                        raise ValueError(
                            f"conflicting duplicate resolved relation_id: {relation_id}"
                        )
                    continue
                resolved_relations_by_id[relation_id] = candidate
                all_relations.append(candidate)

        # Raw Tree-sitter call targets are placeholders and are intentionally omitted unless
        # both endpoints exist in the canonical entity universe. Exact DEFINES edges remain.
        valid_relations = [
            relation for relation in all_relations
            if relation.source_id in entity_id_set and relation.target_id in entity_id_set
        ]

        # Preserve the existing bounded literal chunk policy for FTS retrieval.
        return all_entities, valid_relations, bound_chunks(all_chunks), "SYNTAX_ONLY"
