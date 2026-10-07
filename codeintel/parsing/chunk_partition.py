"""Deterministic source-partitioning for model-visible retrieval chunks.

Tree-sitter entities are hierarchical, while retrieval chunks must not silently
lose repository source or overlap the same source bytes under conflicting entity
ownership.  This module rebuilds retrieval chunks from the canonical entity tree
and source bytes. Language extractors emit entities and relations only; this
module is the single chunk-construction authority for supported source.
"""

from bisect import bisect_right
from dataclasses import dataclass
from typing import Dict, Iterable, List, Sequence, Tuple

from codeintel.core.identity import canonical_hash, make_chunk_id
from codeintel.core.models import Chunk, Entity, EntityKind


@dataclass(frozen=True)
class _OwnedInterval:
    start: int
    end: int
    owner_id: str
    chunk: Chunk


def _line_starts(source: bytes) -> List[int]:
    """Return canonical byte line starts including the exclusive EOF point.

    Tree-sitter advances rows only at LF, including when CR or a Unicode line
    separator appears inside a literal. Keep the terminal offset for legacy
    synthetic ``next-line, 0`` EOF spans; bounded chunks normalize that endpoint.
    """
    starts = [0]
    starts.extend(offset + 1 for offset, value in enumerate(source) if value == 0x0A)
    if starts[-1] != len(source):
        starts.append(len(source))
    return starts


def _point_to_offset(line: int, col: int, starts: Sequence[int], source_len: int) -> int:
    if not isinstance(line, int) or isinstance(line, bool) or line < 1:
        raise ValueError("entity span line must be a positive integer")
    if not isinstance(col, int) or isinstance(col, bool) or col < 0:
        raise ValueError("entity span column must be a non-negative integer")
    if line > len(starts):
        raise ValueError("entity span line exceeds source")
    start = starts[line - 1]
    line_end = starts[line] if line < len(starts) else source_len
    offset = start + col
    if offset > line_end or offset > source_len:
        raise ValueError("entity span column exceeds source line")
    return offset


def _offset_to_point(offset: int, starts: Sequence[int], source_len: int) -> Tuple[int, int]:
    if offset < 0 or offset > source_len:
        raise ValueError("source offset out of range")
    line_index = max(0, bisect_right(starts, offset) - 1)
    # starts intentionally includes the exclusive EOF offset, so an EOF boundary
    # may use the legacy synthetic following-line/column-zero form. Bounded
    # chunk canonicalization later renders the literal LF-only endpoint.
    return line_index + 1, offset - starts[line_index]


def _entity_interval(
    entity: Entity,
    starts: Sequence[int],
    source_len: int,
) -> Tuple[int, int]:
    if entity.kind == EntityKind.MODULE:
        return 0, source_len
    span = entity.span
    if not isinstance(span, tuple) or len(span) != 4:
        raise ValueError(f"invalid entity span for {entity.entity_id}")
    start = _point_to_offset(span[0], span[1], starts, source_len)
    end = _point_to_offset(span[2], span[3], starts, source_len)
    if end < start:
        raise ValueError(f"entity span end precedes start for {entity.entity_id}")
    return start, end


def _non_whitespace(data: bytes) -> bool:
    return bool(data.strip())


def _entity_debug_identity(entity: Entity) -> str:
    """Bounded structural identity for fail-closed parser diagnostics; no source body."""
    parent_id = None
    if isinstance(entity.properties, dict):
        parent_id = entity.properties.get("parent_id")
    kind = entity.kind.value if isinstance(entity.kind, EntityKind) else str(entity.kind)
    return (
        f"qname={entity.qualified_name!r},kind={kind!r},span={tuple(entity.span)!r},"
        f"parent_id={parent_id!r}"
    )


def _make_chunk(
    *,
    source: bytes,
    start: int,
    end: int,
    owner: Entity,
    starts: Sequence[int],
    rel_path: str,
    file_id: str,
    generation_id: str,
) -> _OwnedInterval:
    raw = source[start:end]
    text = raw.decode("utf-8", errors="strict")
    sl, sc = _offset_to_point(start, starts, len(source))
    el, ec = _offset_to_point(end, starts, len(source))
    span = (sl, sc, el, ec)
    content_hash = canonical_hash(text)
    chunk = Chunk(
        chunk_id=make_chunk_id(file_id, generation_id, span, content_hash),
        file_id=file_id,
        generation_id=generation_id,
        rel_path=rel_path,
        span=span,
        content=text,
        content_hash=content_hash,
        entity_ids=[owner.entity_id],
    )
    return _OwnedInterval(start=start, end=end, owner_id=owner.entity_id, chunk=chunk)


def _validate_partition(source: bytes, intervals: Sequence[_OwnedInterval]) -> None:
    ordered = sorted(intervals, key=lambda item: (item.start, item.end, item.owner_id))
    cursor = 0
    for item in ordered:
        if item.start < cursor:
            raise ValueError(
                f"retrieval source ownership overlaps at byte {item.start}: {item.owner_id}"
            )
        if item.start > cursor and _non_whitespace(source[cursor:item.start]):
            raise ValueError(
                f"retrieval source partition omitted non-whitespace bytes {cursor}:{item.start}"
            )
        encoded = item.chunk.content.encode("utf-8")
        if encoded != source[item.start:item.end]:
            raise ValueError("retrieval chunk content does not match canonical source bytes")
        cursor = item.end
    if cursor < len(source) and _non_whitespace(source[cursor:]):
        raise ValueError(
            f"retrieval source partition omitted trailing non-whitespace bytes {cursor}:{len(source)}"
        )


def partition_entity_chunks(
    *,
    entities: Iterable[Entity],
    content: str,
    rel_path: str,
    file_id: str,
    generation_id: str,
) -> List[Chunk]:
    """Return deterministic, non-overlapping chunks covering all meaningful source.

    Every entity owns its source region minus the complete regions owned by its
    immediate child entities.  Leaf entities own their whole definition.  The
    module entity owns all remaining top-level source, including constants,
    comments and statements between/after definitions.  Whitespace-only gaps are
    intentionally not materialized as retrieval chunks.
    """
    if not isinstance(content, str):
        raise TypeError("content must be a string")
    source = content.encode("utf-8", errors="strict")
    entity_list = list(entities)

    entity_by_id: Dict[str, Entity] = {}
    for entity in entity_list:
        prior = entity_by_id.get(entity.entity_id)
        if prior is not None and prior != entity:
            raise ValueError(
                f"conflicting duplicate entity_id {entity.entity_id} in {rel_path}: "
                f"prior=({_entity_debug_identity(prior)}); "
                f"new=({_entity_debug_identity(entity)})"
            )
        entity_by_id[entity.entity_id] = entity

    modules = [entity for entity in entity_list if entity.kind == EntityKind.MODULE]
    if len(modules) != 1:
        raise ValueError("canonical parsed source must contain exactly one module entity")

    starts = _line_starts(source)
    if not source:
        # Preserve the canonical empty module chunk without manufacturing a
        # discarded whole-file chunk for every nonempty source file.
        return [_make_chunk(
            source=source, start=0, end=0, owner=modules[0], starts=starts,
            rel_path=rel_path, file_id=file_id, generation_id=generation_id,
        ).chunk]

    regions: Dict[str, Tuple[int, int]] = {
        entity.entity_id: _entity_interval(entity, starts, len(source))
        for entity in entity_list
    }

    children_by_parent: Dict[str, List[Entity]] = {}
    for entity in entity_list:
        parent_id = entity.properties.get("parent_id") if isinstance(entity.properties, dict) else None
        if parent_id is None:
            continue
        if parent_id not in entity_by_id:
            raise ValueError(
                f"entity {entity.entity_id} references missing parent {parent_id}"
            )
        children_by_parent.setdefault(parent_id, []).append(entity)

    intervals: List[_OwnedInterval] = []
    for owner in sorted(
        entity_list,
        key=lambda entity: (regions[entity.entity_id][0], -regions[entity.entity_id][1], entity.entity_id),
    ):
        owner_start, owner_end = regions[owner.entity_id]
        if owner_end == owner_start:
            continue
        child_regions: List[Tuple[int, int, str]] = []
        for child in children_by_parent.get(owner.entity_id, []):
            child_start, child_end = regions[child.entity_id]
            if child_start < owner_start or child_end > owner_end:
                raise ValueError(
                    f"child entity {child.entity_id} escapes parent {owner.entity_id}"
                )
            child_regions.append((child_start, child_end, child.entity_id))
        child_regions.sort(key=lambda item: (item[0], item[1], item[2]))

        cursor = owner_start
        for child_start, child_end, child_id in child_regions:
            if child_start < cursor:
                raise ValueError(
                    f"sibling entity regions overlap under {owner.entity_id}: {child_id}"
                )
            if child_start > cursor and _non_whitespace(source[cursor:child_start]):
                intervals.append(
                    _make_chunk(
                        source=source,
                        start=cursor,
                        end=child_start,
                        owner=owner,
                        starts=starts,
                        rel_path=rel_path,
                        file_id=file_id,
                        generation_id=generation_id,
                    )
                )
            cursor = child_end
        if cursor < owner_end and _non_whitespace(source[cursor:owner_end]):
            intervals.append(
                _make_chunk(
                    source=source,
                    start=cursor,
                    end=owner_end,
                    owner=owner,
                    starts=starts,
                    rel_path=rel_path,
                    file_id=file_id,
                    generation_id=generation_id,
                )
            )

    _validate_partition(source, intervals)
    ordered = sorted(intervals, key=lambda item: (item.start, item.end, item.owner_id))
    chunk_ids: Dict[str, Tuple[int, int, str]] = {}
    output: List[Chunk] = []
    for item in ordered:
        signature = (item.start, item.end, item.owner_id)
        prior = chunk_ids.get(item.chunk.chunk_id)
        if prior is not None and prior != signature:
            raise ValueError(f"conflicting partition chunk_id: {item.chunk.chunk_id}")
        if prior is None:
            chunk_ids[item.chunk.chunk_id] = signature
            output.append(item.chunk)
    return output


__all__ = ["partition_entity_chunks"]
