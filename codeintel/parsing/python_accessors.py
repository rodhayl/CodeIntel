"""Canonical identity disambiguation for repeated Python methods.

Python intentionally reuses the same qualified name for a property's getter,
setter and deleter. The generic entity identity preimage (file, qname, kind)
therefore collides even though these are distinct source declarations. This
module gives property accessors semantic variants and other disjoint repeated
methods source-order variants. Ambiguous or overlapping duplicates still fail
closed in the partition and storage layers.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from codeintel.core.identity import make_entity_id, make_relation_id
from codeintel.core.models import Entity, EntityKind, Relation


Span = Tuple[int, int, int, int]


def _span_contains(outer: Span, inner: Span) -> bool:
    osl, osc, oel, oec = outer
    isl, isc, iel, iec = inner
    starts_before = osl < isl or (osl == isl and osc <= isc)
    ends_after = oel > iel or (oel == iel and oec >= iec)
    return starts_before and ends_after


def _decorators_for_entity(entity: Entity, content: str) -> List[str]:
    # Entity rows come from Tree-sitter's LF-only byte coordinates.
    lines = content.split("\n")
    start = max(0, entity.span[0] - 1)
    end = min(len(lines), max(start + 1, entity.span[2]))
    decorators: List[str] = []
    for raw in lines[start:end]:
        stripped = raw.strip()
        if stripped.startswith("@"):
            decorators.append(stripped[1:].strip())
            continue
        if stripped.startswith("def ") or stripped.startswith("async def "):
            break
    return decorators


def _accessor_variant(entity: Entity, content: str) -> Optional[Tuple[str, str]]:
    """Return (semantic role, identity variant) for unambiguous property accessors."""
    if entity.kind not in (EntityKind.METHOD, EntityKind.FUNCTION):
        return None
    decorators = _decorators_for_entity(entity, content)
    if not decorators:
        return None

    name = entity.name
    for decorator in decorators:
        if decorator == "property":
            return "getter", "property-getter"
        if decorator == f"{name}.setter" or decorator.endswith(f".{name}.setter"):
            return "setter", "property-setter"
        if decorator == f"{name}.deleter" or decorator.endswith(f".{name}.deleter"):
            return "deleter", "property-deleter"
        if decorator == f"{name}.getter" or decorator.endswith(f".{name}.getter"):
            return "getter", "property-explicit-getter"
    return None


def _choose_group_member(group: Sequence[Entity], span: Span) -> Optional[Entity]:
    exact = [entity for entity in group if tuple(entity.span) == tuple(span)]
    if len(exact) == 1:
        return exact[0]
    containing = [entity for entity in group if _span_contains(tuple(entity.span), tuple(span))]
    if len(containing) != 1:
        return None
    return containing[0]


def normalize_python_property_accessors(
    *,
    entities: Iterable[Entity],
    relations: Iterable[Relation],
    content: str,
) -> Tuple[List[Entity], List[Relation]]:
    """Disambiguate proven property accessors and disjoint repeated methods.

    The canonical ``qualified_name`` remains unchanged. The historic getter ID is
    retained for ``@property``; unambiguous setter/deleter/explicit-getter
    declarations receive an identity-only suffix. Repeated roles and mixed
    accessor/method overrides, like other repeated methods, receive source-order
    suffixes without merging their literal definitions.
    Relations and nested ``parent_id`` references are rebound by source span.
    Ambiguous duplicates remain untouched and still fail closed downstream.
    """
    entity_list = list(entities)
    relation_list = list(relations)
    groups: Dict[str, List[Entity]] = {}
    for entity in entity_list:
        groups.setdefault(entity.entity_id, []).append(entity)

    eligible: Dict[str, Dict[Span, Tuple[str, Optional[str], Optional[str]]]] = {}
    existing_ids = {entity.entity_id for entity in entity_list}
    for old_id, group in groups.items():
        if len(group) < 2:
            continue
        qnames = {entity.qualified_name for entity in group}
        kinds = {entity.kind for entity in group}
        parents = {
            entity.properties.get("parent_id") if isinstance(entity.properties, dict) else None
            for entity in group
        }
        if len(qnames) != 1 or len(kinds) != 1 or len(parents) != 1:
            continue

        spans = [tuple(entity.span) for entity in group]
        if len(set(spans)) != len(spans):
            continue
        ordered_spans = sorted(spans)
        if any(
            (left[2], left[3]) > (right[0], right[1])
            for left, right in zip(ordered_spans, ordered_spans[1:])
        ):
            continue
        accessors = [_accessor_variant(entity, content) for entity in group]
        variants = [accessor[1] for accessor in accessors if accessor is not None]
        mapping: Dict[Span, Tuple[str, Optional[str], Optional[str]]] = {}
        if len(variants) == len(group) and len(set(variants)) == len(variants):
            for entity, accessor in zip(group, accessors):
                assert accessor is not None
                role, variant = accessor
                new_id = (
                    old_id if variant == "property-getter" else make_entity_id(
                        entity.file_id,
                        f"{entity.qualified_name}#python-{variant}",
                        entity.kind.value,
                    )
                )
                if new_id in existing_ids and new_id != old_id:
                    raise ValueError(
                        f"property accessor disambiguation collides with existing entity_id {new_id}"
                    )
                existing_ids.add(new_id)
                mapping[tuple(entity.span)] = (new_id, role, variant)
        elif group[0].kind == EntityKind.METHOD:
            # Disjoint ranges already prove separate declarations. Repeated
            # accessor roles and accessor/plain-method overrides cannot share
            # a semantic accessor ID, but must not make valid source unindexable.
            ordered = sorted(zip(group, accessors), key=lambda item: item[0].span)
            for ordinal, (entity, accessor) in enumerate(ordered, start=1):
                new_id = (
                    old_id if ordinal == 1 else make_entity_id(
                        entity.file_id,
                        f"{entity.qualified_name}#python-method-{ordinal}",
                        entity.kind.value,
                    )
                )
                if new_id in existing_ids and new_id != old_id:
                    raise ValueError(
                        f"repeated method disambiguation collides with existing entity_id {new_id}"
                    )
                existing_ids.add(new_id)
                role, variant = accessor if accessor is not None else (None, None)
                mapping[tuple(entity.span)] = (new_id, role, variant)
        else:
            continue
        eligible[old_id] = mapping

    if not eligible:
        return entity_list, relation_list

    normalized_entities: List[Entity] = []
    normalized_groups: Dict[str, List[Entity]] = {}
    for entity in entity_list:
        mapping = eligible.get(entity.entity_id)
        if mapping is None:
            normalized = entity
        else:
            new_id, role, variant = mapping[tuple(entity.span)]
            properties = dict(entity.properties or {})
            if role is not None and variant is not None:
                properties["python_accessor_role"] = role
                properties["python_accessor_variant"] = variant
            normalized = replace(entity, entity_id=new_id, properties=properties)
        normalized_entities.append(normalized)
        normalized_groups.setdefault(entity.entity_id, []).append(normalized)

    # Rebind nested entities whose old parent ID referred to one of the duplicate
    # declarations. Source containment makes ownership unambiguous for disjoint
    # getter/setter/deleter and repeated method definitions.
    rebound_entities: List[Entity] = []
    for entity in normalized_entities:
        properties = dict(entity.properties or {})
        parent_id = properties.get("parent_id")
        group = normalized_groups.get(parent_id)
        if group and len(group) > 1:
            owner = _choose_group_member(group, tuple(entity.span))
            if owner is None:
                raise ValueError(
                    f"ambiguous nested entity ownership under property accessor group {parent_id}"
                )
            properties["parent_id"] = owner.entity_id
            entity = replace(entity, properties=properties)
        rebound_entities.append(entity)
    normalized_entities = rebound_entities

    by_source_identity: Dict[Tuple[str, str, EntityKind, Span], List[Entity]] = {}
    for entity in normalized_entities:
        identity = (entity.file_id, entity.qualified_name, entity.kind, tuple(entity.span))
        by_source_identity.setdefault(identity, []).append(entity)
    normalized_groups = {}
    for original in entity_list:
        identity = (original.file_id, original.qualified_name, original.kind, tuple(original.span))
        matches = by_source_identity.get(identity, [])
        if len(matches) == 1:
            normalized_groups.setdefault(original.entity_id, []).append(matches[0])

    normalized_relations: List[Relation] = []
    for relation in relation_list:
        source_id = relation.source_id
        target_id = relation.target_id
        source_group = normalized_groups.get(source_id)
        target_group = normalized_groups.get(target_id)
        if source_group and len(source_group) > 1:
            owner = _choose_group_member(source_group, tuple(relation.span))
            if owner is None:
                raise ValueError(
                    f"ambiguous relation source under property accessor group {source_id}"
                )
            source_id = owner.entity_id
        if target_group and len(target_group) > 1:
            target = _choose_group_member(target_group, tuple(relation.span))
            if target is None:
                raise ValueError(
                    f"ambiguous relation target under property accessor group {target_id}"
                )
            target_id = target.entity_id

        new_relation_id = make_relation_id(
            source_id,
            relation.rel_type.value,
            target_id,
            relation.file_id,
            tuple(relation.span),
        )
        normalized_relations.append(
            replace(
                relation,
                relation_id=new_relation_id,
                source_id=source_id,
                target_id=target_id,
            )
        )

    return normalized_entities, normalized_relations


__all__ = ["normalize_python_property_accessors"]
