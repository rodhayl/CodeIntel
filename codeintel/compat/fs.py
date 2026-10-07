"""Filesystem compatibility utilities for CodeIntel."""
from __future__ import annotations

import os


def safe_fchmod(fd: int, mode: int) -> None:
    """Portable file descriptor permission setting.

    Invokes os.fchmod on POSIX platforms where available.
    On Windows or platforms lacking os.fchmod, this is safely a no-op,
    unless os.fchmod has been dynamically provided (e.g. in tests).
    """
    fn = getattr(os, "fchmod", None)
    if fn is not None:
        fn(fd, mode)
