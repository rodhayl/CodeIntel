from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

from codeintel.core.models import Entity, EntityKind, Relation, TrustClass


@dataclass(frozen=True)
class ResolvedCallTarget:
    entity: Entity
    trust_class: TrustClass
    reason: str


@dataclass(frozen=True)
class CallResolutionIndex:
    by_id: Dict[str, Entity]
    targets_by_name: Dict[str, List[Entity]]
    classes: List[Entity]

    @classmethod
    def build(cls, entities: Iterable[Entity]) -> "CallResolutionIndex":
        ordered = _dedupe_entities(entities)
        targets_by_name: Dict[str, List[Entity]] = {}
        classes: List[Entity] = []
        for entity in ordered:
            if entity.kind in (EntityKind.FUNCTION, EntityKind.METHOD, EntityKind.CLASS):
                targets_by_name.setdefault(entity.name, []).append(entity)
            if entity.kind in (EntityKind.CLASS, EntityKind.INTERFACE):
                classes.append(entity)
        return cls(
            by_id={entity.entity_id: entity for entity in ordered},
            targets_by_name=targets_by_name,
            classes=classes,
        )


def _dedupe_entities(items: Iterable[Entity]) -> List[Entity]:
    by_id: Dict[str, Entity] = {}
    for item in items:
        prior = by_id.get(item.entity_id)
        if prior is not None and prior != item:
            raise ValueError(
                f"Conflicting duplicate entity_id in relation resolver: {item.entity_id}"
            )
        by_id[item.entity_id] = item
    return sorted(by_id.values(), key=lambda e: (e.qualified_name, e.file_id, e.span))


def _entity_parent_id(entity: Entity) -> Optional[str]:
    value = entity.properties.get("parent_id")
    if value is None:
        return None
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError(
            f"Entity {entity.entity_id} parent_id must be non-empty NUL-free text or null"
        )
    return value


def _methods_under(targets: Iterable[Entity], parent_id: str) -> List[Entity]:
    return _dedupe_entities(
        t for t in targets if t.kind == EntityKind.METHOD and _entity_parent_id(t) == parent_id
    )


def _lexical_ancestors(entity: Optional[Entity], index: CallResolutionIndex) -> set[str]:
    """Scopes visible from the source; this is not a variable binding analysis."""
    result: set[str] = set()
    while entity is not None and entity.entity_id not in result:
        result.add(entity.entity_id)
        entity = index.by_id.get(_entity_parent_id(entity))
    return result


def _receiver_leaf(receiver: str) -> str:
    parts = [part.strip() for part in receiver.split(".") if part.strip()]
    if parts and parts[-1].casefold() == "prototype":
        parts.pop()
    return parts[-1] if parts else ""


def _call_text(properties: Dict[str, Any], key: str) -> str:
    value = properties.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"CALLS relation {key} metadata must be text or null")
    return value.strip()


def _call_optional_id(properties: Dict[str, Any], key: str) -> Optional[str]:
    value = properties.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError(
            f"CALLS relation {key} metadata must be non-empty NUL-free text or null"
        )
    return value


def resolve_syntactic_call(
    relation: Relation,
    combined_entities: Iterable[Entity] | CallResolutionIndex,
) -> List[ResolvedCallTarget]:
    """Resolve one syntactic CALLS observation with explicit trust semantics.

    RESOLVED means a unique explicit receiver-type match in the syntax IR, not
    compiler or runtime binding proof. Bare names and this/self/class-name
    receivers lack parameter/assignment/import binding evidence and remain
    HEURISTIC. Out-of-scope same-file declarations are omitted. Heuristics stay
    out of authoritative impact queries by default.
    """
    index = (
        combined_entities
        if isinstance(combined_entities, CallResolutionIndex)
        else CallResolutionIndex.build(combined_entities)
    )
    callee = _call_text(relation.properties, "callee_name")
    if not callee:
        return []

    targets = index.targets_by_name.get(callee, [])
    if not targets:
        return []

    source_ent = index.by_id.get(relation.source_id)
    source_parent_id = _entity_parent_id(source_ent) if source_ent else None
    receiver = _call_text(relation.properties, "receiver_name")
    receiver_type = _call_text(relation.properties, "receiver_type")
    raw_is_this = relation.properties.get("is_this", False)
    if not isinstance(raw_is_this, bool):
        raise ValueError("CALLS relation is_this metadata must be boolean")
    is_this = raw_is_this or receiver in ("this", "self", "cls")
    enclosing_cls_id = _call_optional_id(relation.properties, "enclosing_class_id")
    if (
        source_parent_id is not None
        and enclosing_cls_id is not None
        and source_parent_id != enclosing_cls_id
    ):
        raise ValueError(
            "CALLS enclosing_class_id contradicts the canonical source entity parent"
        )
    effective_cls_id = source_parent_id if source_parent_id is not None else enclosing_cls_id
    # Preserve metadata validation even when the enclosing class is absent.
    for target in targets:
        _entity_parent_id(target)

    if is_this:
        enclosing_class = index.by_id.get(effective_cls_id)
        if (
            source_ent is None
            or source_ent.kind != EntityKind.METHOD
            or enclosing_class is None
            or enclosing_class.kind not in (EntityKind.CLASS, EntityKind.INTERFACE)
        ):
            return []
        same_class = _methods_under(targets, effective_cls_id)
        if len(same_class) == 1:
            return [ResolvedCallTarget(same_class[0], TrustClass.HEURISTIC, "same_class_receiver_without_binding")]
        if len(same_class) > 1:
            return [ResolvedCallTarget(t, TrustClass.HEURISTIC, "ambiguous_same_class_overload") for t in same_class]
        return []

    classes = index.classes

    if receiver_type:
        type_leaf = receiver_type.split(".")[-1].strip()
        exact_classes = _dedupe_entities(
            e for e in classes if e.name == type_leaf or e.qualified_name == receiver_type
        )
        typed_targets: List[Entity] = []
        for cls in exact_classes:
            typed_targets.extend(_methods_under(targets, cls.entity_id))
        typed_targets = _dedupe_entities(typed_targets)
        if len(exact_classes) == 1 and len(typed_targets) == 1:
            return [ResolvedCallTarget(typed_targets[0], TrustClass.RESOLVED, "explicit_receiver_type")]
        if typed_targets:
            return [ResolvedCallTarget(t, TrustClass.HEURISTIC, "ambiguous_explicit_receiver_type") for t in typed_targets]

    if receiver:
        receiver_leaf = _receiver_leaf(receiver)
        exact_classes = _dedupe_entities(e for e in classes if e.name == receiver_leaf)
        exact_targets: List[Entity] = []
        for cls in exact_classes:
            exact_targets.extend(_methods_under(targets, cls.entity_id))
        exact_targets = _dedupe_entities(exact_targets)
        if len(exact_classes) == 1 and len(exact_targets) == 1:
            return [ResolvedCallTarget(exact_targets[0], TrustClass.HEURISTIC, "class_name_receiver_without_binding")]
        if exact_targets:
            return [ResolvedCallTarget(t, TrustClass.HEURISTIC, "ambiguous_exact_receiver_class") for t in exact_targets]

    if not receiver:
        visible_scopes = _lexical_ancestors(source_ent, index)
        same_file = _dedupe_entities(
            t for t in targets
            if t.file_id == relation.file_id and t.kind in (EntityKind.FUNCTION, EntityKind.CLASS)
            and _entity_parent_id(t) in visible_scopes
        )
        if len(same_file) == 1:
            return [ResolvedCallTarget(same_file[0], TrustClass.HEURISTIC, "same_file_name_without_binding")]
        if len(same_file) > 1:
            return [ResolvedCallTarget(t, TrustClass.HEURISTIC, "ambiguous_same_file_callable") for t in same_file]
        if (
            len(targets) == 1
            and targets[0].file_id != relation.file_id
            and targets[0].kind in (EntityKind.FUNCTION, EntityKind.CLASS)
        ):
            return [ResolvedCallTarget(targets[0], TrustClass.HEURISTIC, "unique_global_name_without_import_binding")]
        return []

    receiver_leaf = _receiver_leaf(receiver).lower()
    if not receiver_leaf:
        return []
    suffixes = ("service", "controller", "manager", "client", "limiter", "middleware", "provider")
    expected_names = {receiver_leaf, *(receiver_leaf + suffix for suffix in suffixes)}
    candidate_classes = _dedupe_entities(
        cls for cls in classes
        if cls.name.lower() in expected_names
        or cls.name.lower().endswith(receiver_leaf)
        or (len(receiver_leaf) >= 6 and receiver_leaf in cls.name.lower())
    )
    candidate_targets: List[Entity] = []
    for cls in candidate_classes:
        candidate_targets.extend(_methods_under(targets, cls.entity_id))
    return [
        ResolvedCallTarget(t, TrustClass.HEURISTIC, "receiver_naming_convention")
        for t in _dedupe_entities(candidate_targets)
    ]
