"""Canonical bounded retrieval chunks with deterministic UTF-8 byte accounting."""

from __future__ import annotations

from typing import Iterable, List, Tuple

from codeintel.core.contracts import INDEX_CHUNK_POLICY_VERSION, MAX_INDEX_CHUNK_BYTES
from codeintel.core.identity import canonical_hash, make_chunk_id, validate_source_span
from codeintel.core.models import Chunk


def _validate_max_bytes(max_bytes: int) -> int:
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool):
        raise ValueError("max_bytes must be an integer")
    if max_bytes < 128:
        raise ValueError("max_bytes is unreasonably small")
    return max_bytes


def _validate_chunk(chunk: Chunk) -> None:
    if not isinstance(chunk, Chunk):
        raise TypeError("chunk must be a Chunk")
    if not isinstance(chunk.content, str):
        raise TypeError("chunk content must be text")
    if not isinstance(chunk.span, tuple):
        raise ValueError("chunk span must be a four-item tuple")
    validate_source_span(chunk.span)


def _canonical_span_for_content(
    start_line: int,
    start_col: int,
    content: str,
) -> Tuple[int, int, int, int]:
    """Return the Tree-sitter-style end-exclusive byte point for literal content."""
    raw = content.encode("utf-8")
    newline_count = raw.count(b"\n")
    if newline_count == 0:
        return (start_line, start_col, start_line, start_col + len(raw))
    trailing = raw.rsplit(b"\n", 1)[1]
    return (start_line, start_col, start_line + newline_count, len(trailing))


def _canonicalize_chunk_span(chunk: Chunk) -> Chunk:
    """Bind chunk provenance to the byte end-point implied by its literal source."""
    _validate_chunk(chunk)
    start_line, start_col, _, _ = chunk.span
    canonical_span = _canonical_span_for_content(start_line, start_col, chunk.content)
    if canonical_span == chunk.span:
        return chunk
    content_hash = canonical_hash(chunk.content)
    new_id = (
        chunk.chunk_id
        if not chunk.chunk_id.startswith("chk_")
        else make_chunk_id(
            chunk.file_id,
            chunk.generation_id,
            canonical_span,
            content_hash,
        )
    )
    return Chunk(
        chunk_id=new_id,
        file_id=chunk.file_id,
        generation_id=chunk.generation_id,
        rel_path=chunk.rel_path,
        span=canonical_span,
        content=chunk.content,
        content_hash=content_hash,
        entity_ids=list(chunk.entity_ids or []),
    )


def _line_pieces(
    content: str,
    start_line: int,
    start_col: int,
    max_bytes: int = MAX_INDEX_CHUNK_BYTES,
) -> List[Tuple[str, int, int, int, int]]:
    """Return line-preserving pieces as (text, line, start_col, end_col, byte_len)."""
    # Tree-sitter rows advance at LF only. Python splitlines also treats CR,
    # vertical tabs and Unicode separators as rows, corrupting literal spans.
    parts = content.split("\n")
    raw_lines = [part + "\n" for part in parts[:-1]]
    if parts[-1] or not raw_lines:
        raw_lines.append(parts[-1])
    pieces: List[Tuple[str, int, int, int, int]] = []
    line_no = start_line
    for index, full_line in enumerate(raw_lines):
        content_line = full_line[:-1] if full_line.endswith("\n") else full_line
        newline = "\n" if full_line.endswith("\n") else ""
        line_start_col = start_col if index == 0 else 0
        prefix = 0
        byte_column = line_start_col
        while prefix < len(content_line):
            current = ""
            current_bytes = 0
            while prefix < len(content_line):
                ch = content_line[prefix]
                ch_bytes = len(ch.encode("utf-8"))
                newline_bytes = len(newline.encode("utf-8")) if prefix + 1 == len(content_line) else 0
                if current and current_bytes + ch_bytes + newline_bytes > max_bytes:
                    break
                if not current and ch_bytes > max_bytes:
                    raise ValueError("single source character exceeds chunk byte contract")
                current += ch
                current_bytes += ch_bytes
                prefix += 1
            start = byte_column
            end = start + current_bytes
            byte_column = end
            text = current
            if prefix == len(content_line) and newline:
                if len(text.encode("utf-8")) + len(newline.encode("utf-8")) <= max_bytes:
                    text += newline
                    end += len(newline.encode("utf-8"))
                else:
                    pieces.append((text, line_no, start, end, len(text.encode("utf-8"))))
                    start = end
                    text = newline
                    end = start + len(newline.encode("utf-8"))
            pieces.append((text, line_no, start, end, len(text.encode("utf-8"))))
        if content_line == "":
            pieces.append((newline, line_no, line_start_col, line_start_col, len(newline.encode("utf-8"))))
        line_no += full_line.count("\n") or 1
    return pieces


def _segment_lines(
    pieces: List[Tuple[str, int, int, int, int]],
    max_bytes: int = MAX_INDEX_CHUNK_BYTES,
) -> List[List[Tuple[str, int, int, int, int]]]:
    segments: List[List[Tuple[str, int, int, int, int]]] = []
    current: List[Tuple[str, int, int, int, int]] = []
    current_bytes = 0

    for piece in pieces:
        text, _line, _sc, _ec, byte_len = piece
        if current and current_bytes + byte_len > max_bytes:
            segments.append(current)
            current = []
            current_bytes = 0
        current.append(piece)
        current_bytes += byte_len
        if current_bytes > max_bytes:
            raise RuntimeError("chunk segmentation exceeded byte contract")
    if current:
        segments.append(current)
    return segments


def split_chunk(
    chunk: Chunk,
    max_bytes: int = MAX_INDEX_CHUNK_BYTES,
) -> List[Chunk]:
    """Split one literal chunk into bounded source-preserving retrieval units."""
    max_bytes = _validate_max_bytes(max_bytes)
    chunk = _canonicalize_chunk_span(chunk)
    if len(chunk.content.encode("utf-8")) <= max_bytes:
        return [chunk]

    pieces = _line_pieces(chunk.content, chunk.span[0], chunk.span[1], max_bytes=max_bytes)
    segments = _segment_lines(pieces, max_bytes=max_bytes)
    output: List[Chunk] = []
    seen_ids = set()
    for segment in segments:
        text = "".join(item[0] for item in segment)
        text_bytes = len(text.encode("utf-8"))
        if text_bytes > max_bytes:
            raise RuntimeError("bounded chunk exceeds byte contract")
        start_line = segment[0][1]
        start_col = segment[0][2]
        span = _canonical_span_for_content(start_line, start_col, text)
        content_hash = canonical_hash(text)
        chunk_id = make_chunk_id(chunk.file_id, chunk.generation_id, span, content_hash)
        if chunk_id in seen_ids:
            continue
        seen_ids.add(chunk_id)
        output.append(
            Chunk(
                chunk_id=chunk_id,
                file_id=chunk.file_id,
                generation_id=chunk.generation_id,
                rel_path=chunk.rel_path,
                span=span,
                content=text,
                content_hash=content_hash,
                entity_ids=list(chunk.entity_ids or []),
            )
        )

    if "".join(piece[0] for piece in pieces) != chunk.content:
        raise RuntimeError("chunk line decomposition altered literal source")
    return output


def _chunk_signature(chunk: Chunk) -> Tuple[object, ...]:
    return (
        chunk.file_id,
        chunk.generation_id,
        chunk.rel_path,
        tuple(chunk.span),
        chunk.content_hash,
        chunk.content,
        tuple(chunk.entity_ids),
    )


def bound_chunks(
    chunks: Iterable[Chunk],
    max_bytes: int = MAX_INDEX_CHUNK_BYTES,
) -> List[Chunk]:
    """Canonicalize literal provenance, then bound every retrieval chunk deterministically."""
    max_bytes = _validate_max_bytes(max_bytes)
    output: List[Chunk] = []
    seen: dict[str, Tuple[object, ...]] = {}
    for raw_chunk in chunks:
        chunk = _canonicalize_chunk_span(raw_chunk)
        for bounded in split_chunk(chunk, max_bytes=max_bytes):
            signature = _chunk_signature(bounded)
            prior = seen.get(bounded.chunk_id)
            if prior is None:
                seen[bounded.chunk_id] = signature
                output.append(bounded)
            elif prior != signature:
                raise ValueError(
                    f"Conflicting duplicate chunk_id detected: {bounded.chunk_id}"
                )
    return output


__all__ = [
    "INDEX_CHUNK_POLICY_VERSION",
    "MAX_INDEX_CHUNK_BYTES",
    "bound_chunks",
    "split_chunk",
]

