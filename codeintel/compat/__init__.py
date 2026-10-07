"""Cross-platform compatibility shims for CodeIntel."""
from __future__ import annotations

from codeintel.compat import fcntl
from codeintel.compat.fs import safe_fchmod

__all__ = ["fcntl", "safe_fchmod"]
