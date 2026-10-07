import math
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from codeintel.core.identity import canonical_hash, make_evidence_id, normalize_repo_rel_path
from codeintel.core.models import Chunk, Evidence, TrustClass
from codeintel.storage.base import GraphStore, LexicalIndex
from codeintel.storage.policy import DerivedStateValidationError


# These acquisition limits are distinct from the lab's 32 returned candidates.
# They cap materialized exact rows and entity/graph literal bodies, not SQLite
# scan/sort work, source freshness scans, or total query latency.
MAX_EXACT_ENTITY_ROWS = 128
MAX_QUALIFIED_VARIANTS = 16
MAX_ENTITY_CHUNK_ROWS = 4096
MAX_ENTITY_CHUNK_BYTES = 8 * 1024 * 1024


class HybridRetriever:
    """Deterministic RRF retrieval with literal source and truthful signal provenance."""

    def __init__(self, graph_store: GraphStore, lexical_index: LexicalIndex,
                 k_rrf: int = 60):
        if not isinstance(k_rrf, int) or isinstance(k_rrf, bool) or k_rrf <= 0:
            raise ValueError("k_rrf must be a positive integer")
        self._graph_store = graph_store
        self._lexical_index = lexical_index
        self._k_rrf = k_rrf

    @staticmethod
    def _validate_weight(value: float, label: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{label} must be a finite non-negative number")
        normalized = float(value)
        if not math.isfinite(normalized) or normalized < 0.0:
            raise ValueError(f"{label} must be a finite non-negative number")
        return normalized

    @staticmethod
    def _validate_flag(value: bool, label: str) -> bool:
        if not isinstance(value, bool):
            raise ValueError(f"{label} must be boolean")
        return value

    def _canonical_evidence_path(
        self, path: Optional[str], file_id: str, generation_id: str
    ) -> Optional[str]:
        def normalized(candidate):
            # Only the actual opaque identity is not a path. Real source names
            # such as file_utils.py and file_helpers/a.py remain valid.
            if not candidate or str(candidate) == file_id:
                return None
            try:
                return normalize_repo_rel_path(str(candidate))
            except ValueError:
                return None

        resolved = normalized(path)
        if resolved is not None:
            return resolved
        getter = getattr(self._graph_store, "get_file_path", None)
        # The retained storage contract supplies the same canonical path on
        # normal chunks. Only missing/opaque hints need a generation-bound
        # lookup; unexpected backend errors are not missing-path evidence.
        return normalized(getter(file_id, generation_id=generation_id)) if callable(getter) else None

    def _literal_chunk_for_entity(
        self,
        entity: Any,
        generation_id: str,
        chunk_cache: Optional[Dict[str, List[Chunk]]] = None,
        acquisition_budget: Optional[Dict[str, int]] = None,
    ) -> Optional[Chunk]:
        if (
            getattr(entity, "generation_id", None) != generation_id
            or not isinstance(getattr(entity, "file_id", None), str)
            or not getattr(entity, "file_id", None)
        ):
            return None
        if callable(getattr(self._lexical_index, "_get_retrieval_chunks", None)):
            chunks = self._entity_chunks(entity, generation_id, 1,
                                         acquisition_budget, preferred_only=True)
            return chunks[0] if chunks else None
        getter = getattr(self._lexical_index, "get_chunks_for_file", None)
        if not callable(getter):
            return None
        if chunk_cache is not None and entity.file_id in chunk_cache:
            chunks = chunk_cache[entity.file_id]
        else:
            chunks = getter(entity.file_id, generation_id=generation_id)
            # A single retrieval can visit several entities in the same file.
            # Keep only a bounded call-local snapshot; a later retrieval must
            # read storage again so staging changes cannot reuse stale chunks.
            if chunk_cache is not None and len(chunk_cache) < 32:
                chunk_cache[entity.file_id] = chunks
        owning = [
            chunk
            for chunk in chunks
            if isinstance(chunk, Chunk)
            and chunk.generation_id == generation_id
            and chunk.file_id == entity.file_id
            and entity.entity_id in (chunk.entity_ids or [])
        ]
        if not owning:
            return None

        def key(chunk: Chunk) -> Tuple[int, int, int, int, int, str]:
            exact = 0 if tuple(chunk.span) == tuple(entity.span) else 1
            e_line, e_col, _, _ = entity.span
            csl, csc, cel, cec = chunk.span
            contains_start = (
                (csl < e_line or (csl == e_line and csc <= e_col))
                and (cel > e_line or (cel == e_line and cec >= e_col))
            )
            return (
                exact,
                0 if contains_start else 1,
                max(0, int(cel) - int(csl)),
                len((chunk.content or "").encode("utf-8")),
                int(csl),
                chunk.chunk_id,
            )

        owning.sort(key=key)
        return owning[0]

    def _entity_chunks(self, entity, generation_id, limit, budget, *, preferred_only=False):
        if budget is None:
            budget = {"rows": MAX_ENTITY_CHUNK_ROWS, "bytes": MAX_ENTITY_CHUNK_BYTES}
        if not budget["rows"] or not budget["bytes"]:
            return []
        chunks = self._lexical_index._get_retrieval_chunks(
            entity, generation_id, limit=min(limit, budget["rows"]),
            max_bytes=budget["bytes"], preferred_only=preferred_only)
        budget["rows"] -= len(chunks)
        budget["bytes"] -= sum(len(chunk.content.encode("utf-8")) for chunk in chunks)
        return chunks

    def _evidence_from_chunk(
        self,
        chunk: Chunk,
        generation_id: str,
        trust_class: TrustClass = TrustClass.EXACT,
    ) -> Optional[Evidence]:
        if (
            not isinstance(chunk, Chunk)
            or chunk.generation_id != generation_id
            or not isinstance(chunk.file_id, str)
            or not chunk.file_id
        ):
            return None
        content = getattr(chunk, "content", None)
        content_hash = getattr(chunk, "content_hash", None)
        if not isinstance(content, str) or not isinstance(content_hash, str):
            raise RuntimeError("retrieval backend returned invalid literal chunk provenance")
        if content_hash != canonical_hash(content):
            raise DerivedStateValidationError(
                "retrieval backend returned literal chunk bytes with mismatched content hash"
            )
        path = self._canonical_evidence_path(
            getattr(chunk, "rel_path", None), chunk.file_id, generation_id
        )
        if path is None:
            return None
        return Evidence(
            evidence_id=make_evidence_id(
                generation_id, chunk.file_id, chunk.span, content_hash
            ),
            generation_id=generation_id,
            file_id=chunk.file_id,
            path=path,
            span=chunk.span,
            content_hash=content_hash,
            trust_class=trust_class,
            entity_ids=list(chunk.entity_ids or []),
            relation_ids=[],
            reason_selected="",
            source_text=content,
            score=0.0,
        )

    @staticmethod
    def _reason(signals: Set[str], details: List[str]) -> str:
        names = ",".join(sorted(signals)) or "unknown"
        suffix = "; ".join(details[:4])
        return f"signals=[{names}]" + (f"; {suffix}" if suffix else "")

    def retrieve(
        self,
        query: str,
        generation_id: str,
        limit: int = 15,
        lexical_weight: float = 1.0,
        expand_graph: bool = True,
        enable_exact_entity: bool = True,
    ) -> List[Evidence]:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("retrieval query must be a non-empty string")
        if not isinstance(generation_id, str) or not generation_id:
            raise ValueError("retrieval generation_id must be a non-empty string")
        if not isinstance(limit, int) or isinstance(limit, bool):
            raise ValueError("retrieval limit must be an integer")
        if limit <= 0:
            return []
        expand_graph = self._validate_flag(expand_graph, "expand_graph")
        enable_exact_entity = self._validate_flag(
            enable_exact_entity, "enable_exact_entity"
        )
        lexical_weight = self._validate_weight(lexical_weight, "lexical_weight")

        rrf_scores: Dict[str, float] = {}
        evidence_map: Dict[str, Evidence] = {}
        signals: Dict[str, Set[str]] = {}
        details: Dict[str, List[str]] = {}
        chunk_cache: Dict[str, List[Chunk]] = {}
        acquisition_budget = {"rows": MAX_ENTITY_CHUNK_ROWS, "bytes": MAX_ENTITY_CHUNK_BYTES}

        def register(
            cid: str, evidence: Evidence, signal: str, detail: str, increment: float
        ) -> None:
            increment_value = float(increment)
            if not math.isfinite(increment_value):
                raise RuntimeError("retrieval backend produced a non-finite score increment")
            rrf_scores[cid] = rrf_scores.get(cid, 0.0) + increment_value
            signals.setdefault(cid, set()).add(signal)
            details.setdefault(cid, []).append(detail)
            evidence_map.setdefault(cid, evidence)

        lexical_results = self._lexical_index.search(
            query, limit=limit * 2, generation_id=generation_id
        )
        for rank, (chunk, score) in enumerate(lexical_results, 1):
            evidence = self._evidence_from_chunk(chunk, generation_id)
            if evidence is None:
                continue
            register(
                chunk.chunk_id,
                evidence,
                "lexical",
                f"lexical_score={score:.4f}",
                lexical_weight / (self._k_rrf + rank),
            )

        q_clean = query.strip()
        is_ident_like = bool(
            q_clean and " " not in q_clean and all(c.isalnum() or c in "_.:" for c in q_clean)
        )
        if enable_exact_entity and is_ident_like and self._graph_store:
            exact_entities: List[Tuple[Any, str]] = []
            seen_eids: Set[str] = set()
            remaining_entities = MAX_EXACT_ENTITY_ROWS

            def exact_rows(name, *, qualified=False, suffix=None):
                nonlocal remaining_entities
                if not remaining_entities:
                    return []
                bounded_getter = getattr(self._graph_store, "_get_retrieval_entities", None)
                if callable(bounded_getter):
                    rows = bounded_getter(name, generation_id, limit=remaining_entities,
                                          qualified=qualified, suffix=suffix)
                else:
                    # Preserve the existing adapter protocol. The maintained
                    # SQLite backend applies limits before materialization.
                    getter = getattr(self._graph_store, "get_entities_by_qualified_name", None)
                    if qualified and callable(getter):
                        rows = getter(name, generation_id=generation_id)
                    else:
                        rows = self._graph_store.get_entities_by_name(name, generation_id=generation_id)
                        if qualified:
                            rows = [row for row in rows if row.qualified_name == name]
                    if suffix is not None:
                        rows = [row for row in rows if row.qualified_name.endswith(suffix)]
                    rows = rows[:remaining_entities]
                remaining_entities -= len(rows)
                return rows

            q_matches = exact_rows(q_clean, qualified=True)
            for entity in q_matches:
                if getattr(entity, "generation_id", None) != generation_id:
                    continue
                if entity.entity_id not in seen_eids:
                    seen_eids.add(entity.entity_id)
                    exact_entities.append((entity, "exact_qualified_name"))

            # Task prompts commonly name Class.method without the module prefix.
            # Resolve that suffix against indexed definitions, rather than
            # treating it as an exact full qualified name that cannot match.
            if "." in q_clean:
                leaf = q_clean.rsplit(".", 1)[-1]
                suffix = "." + q_clean
                for entity in exact_rows(leaf, suffix=suffix):
                    if (
                        getattr(entity, "generation_id", None) == generation_id
                        and entity.qualified_name.endswith(suffix)
                        and entity.entity_id not in seen_eids
                    ):
                        seen_eids.add(entity.entity_id)
                        exact_entities.append((entity, "exact_qualified_suffix"))

            if "." in q_clean:
                variants = set()
                if ".__init__." in q_clean:
                    variants.add(q_clean.replace(".__init__.", "."))
                parts = q_clean.split(".")
                for i in range(1, min(len(parts), MAX_QUALIFIED_VARIANTS + 1)):
                    variants.add(
                        ".".join(parts[:i]) + ".__init__." + ".".join(parts[i:])
                    )
                for variant in sorted(variants)[:MAX_QUALIFIED_VARIANTS]:
                    matches = exact_rows(variant, qualified=True)
                    for entity in matches:
                        if getattr(entity, "generation_id", None) != generation_id:
                            continue
                        if entity.entity_id not in seen_eids:
                            seen_eids.add(entity.entity_id)
                            exact_entities.append((entity, "exact_qualified_name"))

            for entity in exact_rows(q_clean):
                if getattr(entity, "generation_id", None) != generation_id:
                    continue
                if entity.name == q_clean and entity.entity_id not in seen_eids:
                    seen_eids.add(entity.entity_id)
                    exact_entities.append((entity, "exact_short_name"))

            def exact_sort(item: Tuple[Any, str]) -> Tuple[int, str, int, str]:
                entity, _ = item
                path = self._canonical_evidence_path(
                    entity.properties.get("rel_path")
                    if getattr(entity, "properties", None)
                    else None,
                    entity.file_id,
                    generation_id,
                ) or "~"
                lower = path.lower()
                is_test = 1 if any(
                    token in lower for token in ("/test", "test_", "_test", "tests/")
                ) else 0
                return (is_test, path, entity.span[0], entity.qualified_name)

            exact_entities.sort(key=exact_sort)
            for rank, (entity, match_type) in enumerate(exact_entities, 1):
                # Acquire only this symbol's bounded intersecting prefix. The
                # preferred owned chunk establishes literal ownership as before.
                preferred = self._literal_chunk_for_entity(
                    entity, generation_id, chunk_cache, acquisition_budget)
                if preferred is None:
                    continue
                if callable(getattr(self._lexical_index, "_get_retrieval_chunks", None)):
                    chunks = self._entity_chunks(entity, generation_id, limit, acquisition_budget)
                else:
                    chunks = chunk_cache.get(entity.file_id)
                    if chunks is None:
                        chunks = self._lexical_index.get_chunks_for_file(
                            entity.file_id, generation_id=generation_id)
                start, end = tuple(entity.span[:2]), tuple(entity.span[2:])
                literals = sorted((chunk for chunk in chunks
                    if isinstance(chunk, Chunk) and chunk.generation_id == generation_id
                    and chunk.file_id == entity.file_id
                    and tuple(chunk.span[:2]) < end and tuple(chunk.span[2:]) > start),
                    key=lambda chunk: (chunk.span, chunk.chunk_id))
                for literal in literals[:limit]:
                    evidence = self._evidence_from_chunk(literal, generation_id)
                    if evidence is None:
                        continue
                    cid = literal.chunk_id
                    increment = 1.0 + 1.0 / (self._k_rrf + rank)
                    register(cid, evidence, match_type, match_type, increment)
                    current = evidence_map[cid].symbol_span
                    # If equal names nest, retain the widest requested range.
                    if current is None or (start <= tuple(current[:2]) and end >= tuple(current[2:])):
                        evidence_map[cid].symbol_span = entity.span

        if expand_graph and evidence_map:
            top_cids = sorted(rrf_scores, key=lambda cid: (-rrf_scores[cid], cid))[
                : min(5, max(1, limit))
            ]
            graph_candidate_cap = max(4, min(24, limit * 2))
            graph_candidates_examined = 0

            def _graph_entities(
                method_name: str, entity_id: str, source_text: str = ""
            ) -> List[Tuple[Any, str]]:
                getter = getattr(self._graph_store, method_name, None)
                if not callable(getter):
                    return []
                rows = getter(
                    entity_id,
                    max_depth=1,
                    generation_id=generation_id,
                    include_heuristic=True,
                )
                trust_order = {"EXACT": 0, "RESOLVED": 1, "HEURISTIC": 2}
                entities_by_id: Dict[str, Tuple[Any, str]] = {}
                for row in rows:
                    if len(row) < 2 or getattr(row[1], "generation_id", None) != generation_id:
                        continue
                    entity = row[1]
                    neighbor_id = getattr(entity, "entity_id", None)
                    if not isinstance(neighbor_id, str) or not neighbor_id:
                        continue
                    trust = str(row[2]) if len(row) > 2 else "RESOLVED"
                    prior = entities_by_id.get(neighbor_id)
                    if prior is None or trust_order.get(trust, 3) < trust_order.get(prior[1], 3):
                        entities_by_id[neighbor_id] = (entity, trust)
                return sorted(
                    entities_by_id.values(),
                    key=lambda item: (
                        trust_order.get(item[1], 3),
                        -int(
                            bool(
                                item[0].name
                                and re.search(
                                    rf"\b{re.escape(item[0].name)}\s*\(",
                                    source_text,
                                )
                            )
                        ),
                        (item[0].properties or {}).get("rel_path", item[0].file_id),
                        item[0].span[0],
                        item[0].entity_id,
                    ),
                )

            for cid in top_cids:
                if graph_candidates_examined >= graph_candidate_cap:
                    break
                source_evidence = evidence_map.get(cid)
                if not source_evidence:
                    continue
                for entity_id in source_evidence.entity_ids[:2]:
                    callers = _graph_entities("get_callers", entity_id)
                    callees = _graph_entities(
                        "get_callees", entity_id, source_evidence.source_text or ""
                    )
                    width = max(len(callers), len(callees))
                    for index in range(width):
                        for relation_role, entity_pairs in (("caller", callers), ("callee", callees)):
                            if graph_candidates_examined >= graph_candidate_cap:
                                break
                            if index >= len(entity_pairs):
                                continue
                            entity, rel_trust = entity_pairs[index]
                            graph_candidates_examined += 1
                            literal = self._literal_chunk_for_entity(
                                entity, generation_id, chunk_cache, acquisition_budget
                            )
                            if literal is None:
                                continue
                            try:
                                trust_enum = TrustClass(rel_trust)
                            except (ValueError, TypeError):
                                trust_enum = TrustClass.RESOLVED
                            evidence = self._evidence_from_chunk(
                                literal, generation_id, trust_class=trust_enum
                            )
                            if evidence is None:
                                continue
                            cid2 = literal.chunk_id
                            detail = f"1-hop {relation_role} of {source_evidence.path}"
                            explicit_source = any(
                                signal.startswith("exact_") for signal in signals.get(cid, set())
                            )
                            resolved_callee = (
                                relation_role == "callee"
                                and rel_trust in {"EXACT", "RESOLVED"}
                            )
                            graph_weight = 2.5 if explicit_source and resolved_callee else 0.9
                            graph_increment = graph_weight / (
                                self._k_rrf + graph_candidates_examined
                            )
                            if cid2 in evidence_map:
                                rrf_scores[cid2] = rrf_scores.get(cid2, 0.0) + graph_increment
                                signals.setdefault(cid2, set()).add("graph")
                                details.setdefault(cid2, []).append(detail)
                                continue
                            register(cid2, evidence, "graph", detail, graph_increment)
                        if graph_candidates_examined >= graph_candidate_cap:
                            break

        ordered = sorted(rrf_scores, key=lambda cid: (-rrf_scores[cid], cid))[:limit]
        output: List[Evidence] = []
        for cid in ordered:
            evidence = evidence_map.get(cid)
            if not evidence:
                continue
            score = rrf_scores[cid]
            if not math.isfinite(score):
                raise RuntimeError("retrieval ranking produced a non-finite score")
            evidence.score = score
            evidence.reason_selected = self._reason(
                signals.get(cid, set()), details.get(cid, [])
            )
            output.append(evidence)
        return output
