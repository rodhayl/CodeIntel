"""Real child-startup probes for environment channels that precede tool code."""
import os
import platform
import shutil
import subprocess
import sys

import pytest

from codeintel.core.tooling import sanitized_tool_environment


@pytest.mark.skipif(os.name != "posix", reason="POSIX user-site path layout")
@pytest.mark.parametrize("location", ["userbase", "home"])
@pytest.mark.parametrize("hook", ["pth", "usercustomize"])
def test_sanitized_environment_does_not_execute_user_site_hooks(tmp_path, location, hook):
    home = tmp_path / "home"
    home.mkdir()
    userbase = tmp_path / "custom-userbase" if location == "userbase" else home / ".local"
    site = userbase / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
    site.mkdir(parents=True)
    marker = tmp_path / "startup-executed"
    body = f"import pathlib; pathlib.Path({str(marker)!r}).write_text('executed')\n"
    (site / ("probe.pth" if hook == "pth" else "usercustomize.py")).write_text(body)
    source = {"PATH": os.defpath, "HOME": str(home), "PYTHONNOUSERSITE": ""}
    if location == "userbase":
        source["PYTHONUSERBASE"] = str(userbase)
    executable = getattr(sys, "_base_executable", sys.executable)
    command = [executable, "-c", "print('child-ok')"]

    # Positive attack control proves this host actually processes the hook.
    control = subprocess.run(command, cwd=tmp_path, env=source, capture_output=True, text=True, timeout=5)
    assert control.returncode == 0, control.stderr
    assert marker.exists(), "fixture did not exercise Python's user-site startup"
    marker.unlink()
    result = subprocess.run(
        command, cwd=tmp_path, env=sanitized_tool_environment(source),
        capture_output=True, text=True, timeout=5,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "child-ok"
    assert not marker.exists(), "user-site code ran before the intended validator"


@pytest.mark.skipif(
    sys.platform != "linux" or platform.libc_ver()[0] != "glibc" or shutil.which("cc") is None,
    reason="real ELF audit probe requires Linux/glibc and a C compiler",
)
def test_sanitized_environment_does_not_load_elf_audit_code(tmp_path):
    source_file = tmp_path / "audit.c"
    library = tmp_path / "audit.so"
    marker = tmp_path / "audit-loaded"
    source_file.write_text(r'''
#define _GNU_SOURCE
#include <link.h>
#include <stdlib.h>
#include <fcntl.h>
#include <unistd.h>
unsigned int la_version(unsigned int version) {
    (void)version;
    const char *path = getenv("CODEINTEL_TEST_MARKER");
    int fd = path ? open(path, O_WRONLY | O_CREAT | O_TRUNC, 0600) : -1;
    if (fd >= 0) { (void)write(fd, "loaded", 6); close(fd); }
    return LAV_CURRENT;
}
''')
    build = subprocess.run(
        [shutil.which("cc"), "-shared", "-fPIC", str(source_file), "-o", str(library)],
        capture_output=True, text=True, timeout=15,
    )
    assert build.returncode == 0, build.stderr
    source = {"PATH": os.defpath, "LD_AUDIT": str(library), "CODEINTEL_TEST_MARKER": str(marker)}
    command = [shutil.which("true")]
    control = subprocess.run(command, env=source, capture_output=True, timeout=5)
    assert control.returncode == 0
    assert marker.read_text() == "loaded"
    marker.unlink()
    result = subprocess.run(command, env=sanitized_tool_environment(source), capture_output=True, timeout=5)
    assert result.returncode == 0
    assert not marker.exists(), "ELF audit code ran before the intended tool"


def test_startup_policy_preserves_runtime_configuration_without_mutating_source():
    source = {
        "PYTHONUSERBASE": "/untrusted/user-site", "PYTHONNOUSERSITE": "",
        "LD_AUDIT": "/untrusted/audit.so", "LD_PRELOAD": "/untrusted/preload.so",
        "CUDA_VISIBLE_DEVICES": "0", "LD_LIBRARY_PATH": "/trusted/cuda/lib64",
        "LC_ALL": "C.UTF-8", "HOME": "/trusted/home",
    }
    original = dict(source)
    result = sanitized_tool_environment(source)
    assert source == original
    assert result["PYTHONNOUSERSITE"] == "1"
    assert "PYTHONUSERBASE" not in result
    assert "LD_AUDIT" not in result and "LD_PRELOAD" not in result
    for key in ("CUDA_VISIBLE_DEVICES", "LD_LIBRARY_PATH", "LC_ALL", "HOME"):
        assert result[key] == source[key]
