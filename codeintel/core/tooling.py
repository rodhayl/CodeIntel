"""Trusted executable resolution and sanitized process environments."""

import os
import shutil
import sys
from pathlib import Path
from typing import Dict, Optional


_UNSAFE_ENV_KEYS = {
    "PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "NODE_OPTIONS", "NODE_PATH",
    "BASH_ENV", "ENV", "LD_PRELOAD", "LD_AUDIT", "PYTHONUSERBASE",
    "PYTEST_ADDOPTS", "PYTEST_PLUGINS",
}

if sys.platform == "win32":
    _dirs = []
    for _p in (
        Path(os.environ.get("ProgramFiles", "C:\\Program Files")) / "Git" / "cmd",
        Path(os.environ.get("ProgramFiles", "C:\\Program Files")) / "Git" / "bin",
        Path(os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)")) / "Git" / "cmd",
        Path(os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)")) / "Git" / "bin",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Git" / "cmd",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Git" / "bin",
        Path(os.environ.get("SystemRoot", "C:\\Windows")) / "System32",
        Path(os.environ.get("SystemRoot", "C:\\Windows")),
    ):
        if _p.exists() and _p not in _dirs:
            _dirs.append(_p)
    for _tool_name in ("git",):
        _w = shutil.which(_tool_name)
        if _w:
            _wp = Path(_w).parent
            if _wp.exists() and _wp not in _dirs:
                _dirs.append(_wp)
    _SYSTEM_TOOL_DIRS = tuple(_dirs)
    _TRUSTED_SYSTEM_TOOL_TARGET_DIRS = _SYSTEM_TOOL_DIRS
    _CANONICAL_TOOL_PATH = os.pathsep.join(str(path) for path in _SYSTEM_TOOL_DIRS)
else:
    _SYSTEM_TOOL_DIRS = tuple(Path(path) for path in ("/usr/bin", "/bin", "/usr/local/bin"))
    _TRUSTED_SYSTEM_TOOL_TARGET_DIRS = _SYSTEM_TOOL_DIRS
    _CANONICAL_TOOL_PATH = os.pathsep.join(str(path) for path in _SYSTEM_TOOL_DIRS)


def _resolved_executable_within(candidate: Path, allowed_roots: tuple[Path, ...]) -> Optional[str]:
    try:
        resolved = candidate.resolve(strict=True)
        if not resolved.is_file() or not os.access(resolved, os.X_OK):
            return None
        canonical_roots = tuple(root.resolve(strict=False) for root in allowed_roots)
        if not any(resolved.is_relative_to(root) for root in canonical_roots):
            return None
        return str(resolved)
    except (OSError, RuntimeError):
        return None


def resolve_tool_binary(name: str) -> Optional[str]:
    if not isinstance(name, str) or not name or "/" in name or "\\" in name or "\x00" in name:
        raise ValueError("tool name must be one plain path component")
    if name != "git":
        return None  # The offline runtime has no other external toolchain.
    suffixes = (".cmd", ".exe", ".bat", "") if sys.platform == "win32" else ("",)
    for sys_dir in _SYSTEM_TOOL_DIRS:
        for sfx in suffixes:
            candidate = sys_dir / f"{name}{sfx}"
            # Accept only an executable whose resolved target stays in trusted
            # system locations; no project-local or Node/npm toolchains.
            resolved = _resolved_executable_within(candidate, _TRUSTED_SYSTEM_TOOL_TARGET_DIRS)
            if resolved is not None:
                return resolved
    return None


def sanitized_tool_environment(source: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    env = dict(os.environ if source is None else source)
    for key in list(env):
        if (
            key in _UNSAFE_ENV_KEYS
            or key.startswith("DYLD_")
            or key.startswith("GIT_")
        ):
            env.pop(key, None)
    env["PATH"] = _CANONICAL_TOOL_PATH
    # User-site .pth/customization hooks run before the selected Python tool.
    env["PYTHONNOUSERSITE"] = "1"
    # Host-installed pytest plugins and environment addopts must not change which
    # canonical release tests execute or their result.
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    # Git environment can redirect repository/index/config identity or select helper
    # executables before a caller-supplied cwd is honored. Keep repository-local
    # configuration discoverable through cwd while excluding host/user authorities.
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_ATTR_NOSYSTEM"] = "1"
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_PAGER"] = "cat"
    # Repository-local core.fsmonitor may execute an untrusted helper even for read-only
    # commands such as git ls-files. Override only that execution-capable setting while
    # keeping benign repository-local configuration visible.
    env["GIT_CONFIG_COUNT"] = "1"
    env["GIT_CONFIG_KEY_0"] = "core.fsmonitor"
    env["GIT_CONFIG_VALUE_0"] = "false"
    return env
