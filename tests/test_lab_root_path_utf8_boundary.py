"""CLI root-path decoding failures are input errors before state mutation."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import codeintel

from codeintel.lab.retrieval import index_repository, query_repository, serialize


RUNTIME_ROOT = Path(codeintel.__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX raw path arguments")


def _run(command, *, cwd=None, env=None):
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    environment.update(env or {})
    program = (f"import sys; sys.path.insert(0, {str(RUNTIME_ROOT)!r}); "
               "from codeintel.lab.cli import main; main()")
    return subprocess.run(
        [os.fsencode(sys.executable), b"-I", b"-B", b"-c", program.encode(), *command],
        cwd=cwd, env=environment, capture_output=True, timeout=10, check=False,
    )


def _assert_input_error(result, code):
    assert result.returncode == 2, result.stderr
    assert result.stdout == b""
    error = json.loads(result.stderr)
    assert set(error) == {"status", "code", "recovery"}
    assert error["status"] == "ERROR" and error["code"] == code
    assert b"Traceback" not in result.stderr
    assert b"private_root" not in result.stderr


@pytest.mark.parametrize("command", ["index", "query"])
@pytest.mark.parametrize("kind", ["repo", "repo-alias", "repo-cwd", "state", "state-cwd", "state-home"])
def test_non_utf8_effective_roots_fail_before_creating_state_or_receipt(tmp_path, command, kind):
    bad = os.fsencode(tmp_path) + b"/private_root_\xff"
    os.mkdir(bad)
    repo = bad if kind.startswith("repo") else os.fsencode(tmp_path / "repo")
    os.makedirs(repo, exist_ok=True)
    with open(repo + b"/source.py", "wb") as stream:
        stream.write(b"def target(): return 1\n")
    repo_arg, state_arg = repo, os.fsencode(tmp_path / "state")
    state_path = state_arg
    cwd, env = None, {}
    if kind == "repo-alias":
        repo_arg = os.fsencode(tmp_path / "alias")
        os.symlink(repo, repo_arg)
    elif kind == "repo-cwd":
        repo_arg, cwd = b".", repo
    elif kind == "state":
        state_arg = state_path = bad + b"/state"
    elif kind == "state-cwd":
        state_arg, state_path, cwd = b"state", bad + b"/state", bad
    elif kind == "state-home":
        state_arg, state_path = b"~/state", bad + b"/state"
        env["HOME"] = os.fsdecode(bad)
    record = tmp_path / "receipt.json"
    args = [command.encode(), b"--repo", repo_arg, b"--state-dir", state_arg,
            b"--record", os.fsencode(record)]
    if command == "query":
        args.append(b"target")
    result = _run(args, cwd=cwd, env=env)
    _assert_input_error(result, "SOURCE_UNAVAILABLE" if kind.startswith("repo") else "INVALID_STATE_PATH")
    assert not os.path.exists(state_path)
    assert not record.exists()


@pytest.mark.parametrize("alias", [False, True])
def test_verify_keeps_safe_posix_root_spelling_without_service_identity(tmp_path, alias):
    repo, state = tmp_path / "repo", tmp_path / "state"
    repo.mkdir()
    (repo / "source.py").write_text("def target(): return 1\n")
    index_repository(repo, state)
    packet, _ = query_repository(repo, state, "target")
    packet_path = tmp_path / "packet.json"
    packet_path.write_bytes(serialize(packet))
    bad = os.fsencode(tmp_path) + b"/private_root_\xff"
    os.rename(repo, bad)
    root_arg = bad
    if alias:
        root_arg = os.fsencode(tmp_path / "alias")
        os.symlink(bad, root_arg)
    result = _run([b"verify", b"--repo", root_arg, b"--packet", os.fsencode(packet_path)])
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "VERIFIED"


def test_normal_unicode_root_paths_keep_index_query_verify_and_receipts(tmp_path):
    repo, state = tmp_path / "café", tmp_path / "状態"
    repo.mkdir()
    (repo / "source.py").write_text("def target(): return 1\n")
    common = [b"--repo", os.fsencode(repo), b"--state-dir", os.fsencode(state)]
    index_record, query_record = tmp_path / "index-é.json", tmp_path / "query-é.json"
    indexed = _run([b"index", *common, b"--record", os.fsencode(index_record)])
    assert indexed.returncode == 0, indexed.stderr
    queried = _run([b"query", *common, b"target", b"--record", os.fsencode(query_record)])
    assert queried.returncode == 0, queried.stderr
    packet_path = tmp_path / "packet.json"
    packet_path.write_bytes(queried.stdout)
    verified = _run([b"verify", b"--repo", os.fsencode(repo), b"--packet", os.fsencode(packet_path)])
    assert verified.returncode == 0, verified.stderr
    assert json.loads(verified.stdout)["status"] == "VERIFIED"
    for record in (index_record, query_record):
        assert json.loads(record.read_bytes())["delivery"] == "prepared_for_stdout"


@pytest.mark.parametrize("target", ["repo", "state"])
def test_root_symlink_loop_still_has_structured_recovery(tmp_path, target):
    repo, state = tmp_path / "repo", tmp_path / "state"
    if target == "repo":
        repo.symlink_to(repo.name)
    else:
        repo.mkdir()
        state.symlink_to(state.name)
    result = _run([b"index", b"--repo", os.fsencode(repo), b"--state-dir", os.fsencode(state)])
    assert result.returncode == 2 and result.stdout == b""
    error = json.loads(result.stderr)
    expected = {"REPOSITORY_MISSING"} if target == "repo" else {"SecurityException", "INVALID_STATE_PATH"}
    assert error["code"] in expected
    assert b"Traceback" not in result.stderr
    assert (repo if target == "repo" else state).is_symlink()
