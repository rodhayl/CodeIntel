import os

from codeintel.core.tooling import _CANONICAL_TOOL_PATH, sanitized_tool_environment


def test_sanitized_tool_environment_replaces_untrusted_path():
    env = sanitized_tool_environment(
        {
            "PATH": "/repo/bin:/tmp/evil",
            "HOME": "/home/test",
            "NODE_OPTIONS": "--require /tmp/evil.js",
        }
    )

    assert env["PATH"] == _CANONICAL_TOOL_PATH
    assert env["HOME"] == "/home/test"
    assert "NODE_OPTIONS" not in env
