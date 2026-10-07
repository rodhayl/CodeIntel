from __future__ import annotations

from codeintel.core.tooling import sanitized_tool_environment


def test_release_tool_environment_removes_pytest_injection():
    env = sanitized_tool_environment(
        {
            "HOME": "/tmp/home",
            "PATH": "/tmp/host-tools",
            "PYTEST_ADDOPTS": "--ignore=tests/failing",
            "PYTEST_PLUGINS": "host_plugin",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "0",
        }
    )
    assert "PYTEST_ADDOPTS" not in env
    assert "PYTEST_PLUGINS" not in env
    assert env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
    assert env["HOME"] == "/tmp/home"


def test_unrelated_environment_is_preserved():
    env = sanitized_tool_environment({"HOME": "/safe/home", "LANG": "C.UTF-8"})
    assert env["HOME"] == "/safe/home"
    assert env["LANG"] == "C.UTF-8"
