"""Deliberate duplicate used to inspect full-fragment deduplication."""


def reserve_seats(available: int, requested: int) -> int:
    """Reserve seats after checking inventory and a positive quantity."""
    if requested <= 0:
        raise ValueError("requested must be positive")
    if requested > available:
        raise ValueError("insufficient inventory")
    return available + requested
