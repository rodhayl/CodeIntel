import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from codeintel.lab.retrieval import (LabError, index_repository, literal_span,
                                     query_repository, serialize, verify_packet)
from codeintel.lab.scenarios import FIXTURE, evaluate, run_demo


@pytest.fixture
def indexed(tmp_path):
    repo, state = tmp_path / "repository", tmp_path / "state"
    shutil.copytree(FIXTURE, repo, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    index_repository(repo, state)
    return repo, state


def test_relevance_provenance_dedup_and_private_telemetry(indexed):
    repo, state = indexed
    packet, audit = query_repository(repo, state, "reserve_seats")
    verify_packet(repo, packet)
    assert packet["selected"][0]["path"] == "booking.py"
    assert "insufficient inventory" in packet["selected"][0]["source"]
    assert "exact_short_name" in packet["selected"][0]["reason"]
    assert packet["omitted"]["duplicate"] == 1
    assert audit["packet_bytes"] == len(serialize(packet)) <= 4096
    assert audit["agent_received"] is audit["agent_retained"] is audit["tokens"] is None
    assert "source" not in audit["selected"][0]
    assert str(repo) not in serialize(audit).decode()
    assert "return available" not in serialize(audit).decode()


def test_no_index_and_reuse(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("def hello():\n    return 1\n")
    with pytest.raises(LabError, match="INDEX_REQUIRED"):
        query_repository(repo, tmp_path / "state", "hello")
    first, _ = index_repository(repo, tmp_path / "state")
    second, _ = index_repository(repo, tmp_path / "state")
    assert first["snapshot_sha256"] == second["snapshot_sha256"]
    assert second["status"] == "REUSED_EXISTING"


def test_edit_delete_add_and_tampered_packet(indexed):
    repo, state = indexed
    old, _ = query_repository(repo, state, "reserve_seats")
    old["selected"][0]["source"] += "tampered"
    with pytest.raises(LabError, match="PROVENANCE_MISMATCH"):
        verify_packet(repo, old)
    old, _ = query_repository(repo, state, "reserve_seats")
    (repo / "booking.py").write_text("def reserve_seats(a, n):\n    return a - n\n")
    with pytest.raises(LabError, match="STALE_SOURCE"):
        verify_packet(repo, old)
    fresh, _ = query_repository(repo, state, "reserve_seats")
    assert fresh["refreshed"]
    assert old["snapshot_sha256"] != fresh["snapshot_sha256"]
    assert "return a - n" in fresh["selected"][0]["source"]
    (repo / "billing.ts").unlink()
    deleted, _ = query_repository(repo, state, "invoiceTotal")
    assert deleted["status"] == "EMPTY" and deleted["refreshed"]
    (repo / "new.py").write_text("def new_symbol():\n    return 42\n")
    added, _ = query_repository(repo, state, "new_symbol")
    assert added["refreshed"] and added["selected"][0]["path"] == "new.py"


@pytest.mark.parametrize("budget", [1024, 2048, 4096, 8192])
def test_complete_utf8_packet_limits(indexed, budget):
    repo, state = indexed
    packet, _ = query_repository(repo, state, "reserve_seats", max_bytes=budget)
    assert len(serialize(packet)) <= budget
    for row in packet["selected"]:
        assert row["source"].endswith("return available + requested")
    if budget == 1024:
        assert packet["status"] == "BUDGET_EXHAUSTED"
        assert packet["omitted"]["budget"]


@pytest.mark.parametrize("kwargs,code", [({"max_bytes": 0}, "INVALID_BUDGET"),
                                         ({"max_bytes": True}, "INVALID_BUDGET"),
                                         ({"limit": 21}, "INVALID_LIMIT")])
def test_invalid_limits(indexed, kwargs, code):
    with pytest.raises(LabError, match=code):
        query_repository(*indexed, "reserve_seats", **kwargs)


def test_unicode_crlf_literal_span():
    raw = 'def café():\r\n    return "sí"\r\n'.encode()
    assert literal_span(raw, [1, 0, 3, 0]) == raw
    assert literal_span(raw, [1, 4, 1, 9]).decode() == "café"
    with pytest.raises(LabError, match="INVALID_SPAN"):
        literal_span(raw, [1, 0, 9, 0])


def test_packet_schema_and_traversal_fail_closed(indexed):
    import copy
    repo, state = indexed
    packet, _ = query_repository(repo, state, "reserve_seats")
    for mutate in (lambda p: p.update(unknown_source="unbound"),
                   lambda p: p["selected"][0].update(path="../outside.py"),
                   lambda p: p["selected"][0].update(span=[0, 0, 1, 1]),
                   lambda p: p["limits"].update(fragments=True),
                   lambda p: p["selected"].append(p["selected"][0])):
        bad = copy.deepcopy(packet)
        mutate(bad)
        with pytest.raises(LabError, match="INVALID_PACKET"):
            verify_packet(repo, bad)


def test_demo_and_comparable_offline_evaluation(tmp_path):
    demo = run_demo(tmp_path / "demo")
    assert demo["status"] == "PASS"
    assert demo["steps"][4]["result"] == "STALE_SOURCE"
    with pytest.raises(FileExistsError):
        run_demo(tmp_path / "demo")
    result = evaluate()
    assert result["status"] == "PASS"
    assert len(result["rows"]) == 24
    assert result["model_calls"] == 0 and result["agent_savings"] is None
    assert {r["arm"] for r in result["rows"]} == {"ranked", "literal_scan"}


def test_missing_optional_dependencies_and_network_forbidden(tmp_path):
    script = '''
import sys, socket
sys.modules["watchdog"] = None
sys.modules["google.protobuf"] = None
def forbidden(*a, **kw):
    raise AssertionError("network was used")
socket.socket.connect = forbidden
socket.socket.connect_ex = forbidden
socket.create_connection = forbidden
from pathlib import Path
from codeintel.lab.scenarios import run_demo
assert run_demo(Path(sys.argv[1]))["status"] == "PASS"
'''
    run = subprocess.run([sys.executable, "-c", script, str(tmp_path / "offline")],
                         capture_output=True, text=True, timeout=20)
    assert run.returncode == 0, run.stderr


def test_cli_emission_receipt_and_recovery(indexed, tmp_path):
    repo, state = indexed
    record = tmp_path / "record.json"
    command = [sys.executable, "-c", "from codeintel.lab.entry import main; main()", "lab", "query",
               "--repo", str(repo), "--state-dir", str(state), "reserve_seats", "--record", str(record)]
    run = subprocess.run(command, capture_output=True, timeout=20)
    assert run.returncode == 0, run.stderr
    assert len(run.stdout) <= 4096
    receipt = json.loads(record.read_text())
    assert receipt["delivery"] == "prepared_for_stdout" and receipt["agent_received"] is None
    retry = subprocess.run(command, capture_output=True, timeout=20)
    assert retry.returncode == 2 and retry.stdout == b""
    assert json.loads(retry.stderr)["code"] == "RECORD_EXISTS"


def test_symlink_and_state_inside_repo_rejected(indexed, tmp_path):
    repo, state = indexed
    packet, _ = query_repository(repo, state, "reserve_seats")
    selected = repo / packet["selected"][0]["path"]
    selected.unlink()
    selected.symlink_to(tmp_path / "outside.py")
    (tmp_path / "outside.py").write_text("private unrelated source")
    with pytest.raises(LabError, match="SOURCE_UNAVAILABLE"):
        verify_packet(repo, packet)
    from codeintel.core.security import SecurityException
    with pytest.raises(SecurityException):
        index_repository(repo, repo / "state")


@pytest.mark.parametrize("operation", [index_repository, query_repository],
                         ids=["index", "query"])
@pytest.mark.parametrize("link_kind", ["state-directory", "ancestor-directory"])
def test_state_symlinks_rejected_without_target_changes(indexed, tmp_path, operation,
                                                       link_kind):
    from codeintel.core.security import SecurityException

    repo, state = indexed
    # A valid existing state makes both index and query capable of mutating it
    # if the adapter resolves away the link before the trusted-state guard.
    state.chmod(0o755)
    sentinel = state / "untouched.txt"
    sentinel.write_bytes(b"keep this state directory unchanged\n")
    sentinel.chmod(0o640)
    if link_kind == "state-directory":
        link = tmp_path / "linked-state"
        link.symlink_to(state, target_is_directory=True)
        requested_state = link
    else:
        link = tmp_path / "linked-parent"
        link.symlink_to(tmp_path, target_is_directory=True)
        requested_state = link / state.name

    def snapshot():
        return {
            path.relative_to(state).as_posix(): (
                path.stat().st_mode,
                path.read_bytes() if path.is_file() else None,
            )
            for path in [state, *sorted(state.rglob("*"))]
        }

    before = snapshot()
    parent_mode = tmp_path.stat().st_mode
    args = (repo, requested_state)
    if operation is query_repository:
        args += ("reserve_seats",)
    with pytest.raises(SecurityException):
        operation(*args)
    assert link.is_symlink()
    assert snapshot() == before
    assert tmp_path.stat().st_mode == parent_mode


# The first twelve cases reproduce the independent review's accepted malformed
# packets. Remaining cases cover adjacent nested-schema boundaries.
_PACKET_SCHEMA_MUTATIONS = [
    pytest.param(("snapshot_sha256",), None, id="null-snapshot-hash"),
    pytest.param(("refreshed",), "yes", id="nonboolean-refreshed"),
    pytest.param(("version",), None, id="null-version"),
    pytest.param(("status",), "BOGUS", id="invalid-status"),
    pytest.param(("status",), "EMPTY", id="empty-status-with-selected-source"),
    pytest.param(("omitted",), None, id="null-omitted"),
    pytest.param(("omitted", "budget"), -1, id="negative-omission-count"),
    pytest.param(("limits", "unbound_source"), "unverified text", id="unknown-nested-limits-key"),
    pytest.param(("limits", "unit"), "tokens", id="wrong-limit-unit"),
    pytest.param(("limits", "candidates"), -1, id="wrong-candidate-count"),
    pytest.param(("selected", 0, "score"), {"unbound_source": "unverified text"},
                 id="nonnumeric-score"),
    pytest.param(("selected", 0, "reason"), ["unverified text"], id="nontext-reason"),
    pytest.param(("schema",), "codeintel-lab-packet-v999", id="unknown-schema"),
    pytest.param(("version",), "", id="empty-version"),
    pytest.param(("version",), 1, id="numeric-version"),
    pytest.param(("status",), None, id="null-status"),
    pytest.param(("status",), "BUDGET_EXHAUSTED", id="budget-status-with-selected-source"),
    pytest.param(("refreshed",), 1, id="integer-refreshed"),
    pytest.param(("selection",), None, id="null-selection"),
    pytest.param(("selection",), {"source": "unbound"}, id="object-selection"),
    pytest.param(("selection",), "unknown ranking", id="unknown-selection"),
    pytest.param(("limits",), [], id="list-limits"),
    pytest.param(("limits", "packet_bytes"), True, id="boolean-packet-budget"),
    pytest.param(("limits", "packet_bytes"), 4096.0, id="float-packet-budget"),
    pytest.param(("limits", "fragments"), True, id="boolean-fragment-limit"),
    pytest.param(("limits", "fragments"), "5", id="text-fragment-limit"),
    pytest.param(("limits", "candidates"), True, id="boolean-candidate-limit"),
    pytest.param(("limits", "candidates"), 32.0, id="float-candidate-limit"),
    pytest.param(("limits", "candidates"), 33, id="unsupported-candidate-limit"),
    pytest.param(("limits", "unit"), None, id="null-unit"),
    pytest.param(("omitted",), [], id="list-omitted"),
    pytest.param(("omitted", "unbound_source"), "unverified text", id="unknown-omitted-key"),
    pytest.param(("omitted", "duplicate"), True, id="boolean-omission-count"),
    pytest.param(("omitted", "budget"), 1.0, id="float-omission-count"),
    pytest.param(("omitted", "limit"), "1", id="text-omission-count"),
    pytest.param(("omitted", "limit"), None, id="null-omission-count"),
    pytest.param(("omitted", "budget"), 33, id="excess-omission-count"),
    pytest.param(("selected",), {}, id="object-selected"),
    pytest.param(("selected", 0), None, id="null-selected-row"),
    pytest.param(("selected", 0, "unbound_source"), "unverified text", id="unknown-row-key"),
    pytest.param(("selected", 0, "path"), None, id="null-row-path"),
    pytest.param(("selected", 0, "path"), "../outside.py", id="traversal-row-path"),
    pytest.param(("selected", 0, "path"), "/outside.py", id="absolute-row-path"),
    pytest.param(("selected", 0, "span"), [1, 0, 2], id="short-span"),
    pytest.param(("selected", 0, "span"), [True, 0, 2, 0], id="boolean-span-coordinate"),
    pytest.param(("selected", 0, "span"), [1, 0, 0, 0], id="reversed-span"),
    pytest.param(("selected", 0, "source"), None, id="null-row-source"),
    pytest.param(("selected", 0, "score"), True, id="boolean-score"),
    pytest.param(("selected", 0, "score"), "1.0", id="text-score"),
    pytest.param(("selected", 0, "score"), float("nan"), id="nan-score"),
    pytest.param(("selected", 0, "score"), float("inf"), id="infinite-score"),
    pytest.param(("selected", 0, "score"), float("-inf"), id="negative-infinite-score"),
]


def _assert_invalid_packet_before_source_read(repo, packet, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("source was read before the entire packet schema was validated")

    monkeypatch.setattr("codeintel.lab.retrieval.source_bytes", forbidden)
    with pytest.raises(LabError, match="^INVALID_PACKET$"):
        verify_packet(repo, packet)


@pytest.mark.parametrize("field_path,value", _PACKET_SCHEMA_MUTATIONS)
def test_nested_packet_schema_rejected_before_source_read(indexed, monkeypatch,
                                                         field_path, value):
    repo, state = indexed
    packet, _ = query_repository(repo, state, "reserve_seats")
    target = packet
    for key in field_path[:-1]:
        target = target[key]
    target[field_path[-1]] = value
    _assert_invalid_packet_before_source_read(repo, packet, monkeypatch)


@pytest.mark.parametrize("section,key", [
    ("limits", "packet_bytes"), ("limits", "fragments"),
    ("limits", "candidates"), ("limits", "unit"),
    ("omitted", "duplicate"), ("omitted", "budget"), ("omitted", "limit"),
])
def test_missing_nested_packet_keys_fail_closed(indexed, monkeypatch, section, key):
    repo, state = indexed
    packet, _ = query_repository(repo, state, "reserve_seats")
    del packet[section][key]
    _assert_invalid_packet_before_source_read(repo, packet, monkeypatch)


@pytest.mark.parametrize("field_path", [
    ("snapshot_sha256",), ("selected", 0, "source_sha256"),
    ("selected", 0, "file_sha256"),
], ids=["snapshot", "source", "file"])
@pytest.mark.parametrize("value", [None, "0" * 63, "0" * 65, "g" * 64, "A" * 64],
                         ids=["null", "short", "long", "nonhex", "uppercase"])
def test_packet_digest_syntax_checked_before_source_read(indexed, monkeypatch,
                                                        field_path, value):
    repo, state = indexed
    packet, _ = query_repository(repo, state, "reserve_seats")
    target = packet
    for key in field_path[:-1]:
        target = target[key]
    target[field_path[-1]] = value
    _assert_invalid_packet_before_source_read(repo, packet, monkeypatch)


@pytest.mark.parametrize("mutation", ["score", "span", "extra-key", "duplicate"])
def test_malformed_later_row_rejected_before_any_source_read(indexed, monkeypatch,
                                                           mutation):
    import copy

    repo, state = indexed
    packet, _ = query_repository(repo, state, "reserve_seats")
    row = copy.deepcopy(packet["selected"][0])
    if mutation != "duplicate":
        row["source_sha256"] = "0" * 64
    if mutation == "score":
        row["score"] = {"unbound_source": "unverified text"}
    elif mutation == "span":
        row["span"] = [0, 0, 1, 0]
    elif mutation == "extra-key":
        row["unbound_source"] = "unverified text"
    packet["selected"].append(row)
    assert len(packet["selected"]) <= packet["limits"]["fragments"]
    assert len(serialize(packet)) <= packet["limits"]["packet_bytes"]
    _assert_invalid_packet_before_source_read(repo, packet, monkeypatch)


@pytest.mark.parametrize("status,omitted", [
    ("OK", {"duplicate": 0, "budget": 0, "limit": 0}),
    ("EMPTY", {"duplicate": 0, "budget": 1, "limit": 0}),
    ("EMPTY", {"duplicate": 1, "budget": 0, "limit": 0}),
    ("BUDGET_EXHAUSTED", {"duplicate": 0, "budget": 0, "limit": 0}),
    ("BUDGET_EXHAUSTED", {"duplicate": 0, "budget": 0, "limit": 1}),
], ids=["ok-without-source", "empty-with-budget-omissions", "empty-with-duplicates",
        "budget-without-omissions", "budget-with-only-limit-omissions"])
def test_empty_packet_status_and_omissions_are_consistent(indexed, monkeypatch,
                                                        status, omitted):
    repo, state = indexed
    packet, _ = query_repository(repo, state, "reserve_seats")
    packet.update(status=status, selected=[], omitted=omitted)
    _assert_invalid_packet_before_source_read(repo, packet, monkeypatch)


@pytest.mark.parametrize("baseline", [False, True], ids=["ranked", "literal"])
@pytest.mark.parametrize("query,budget,expected", [
    ("reserve_seats", 4096, "OK"),
    ("reserve_seats", 1024, "BUDGET_EXHAUSTED"),
    ("no_such_symbol_7fe3c9", 4096, "EMPTY"),
])
def test_generated_packet_statuses_round_trip(indexed, baseline, query, budget, expected):
    repo, state = indexed
    packet, _ = query_repository(repo, state, query, max_bytes=budget, baseline=baseline)
    assert packet["status"] == expected
    verify_packet(repo, json.loads(serialize(packet)))


@pytest.mark.parametrize("score", [0, -1, 1.25])
def test_finite_numeric_scores_remain_valid(indexed, score):
    repo, state = indexed
    packet, _ = query_repository(repo, state, "reserve_seats")
    packet["selected"][0]["score"] = score
    verify_packet(repo, packet)


@pytest.mark.parametrize("command", ["index", "query"])
def test_cli_invalid_utf8_source_has_sanitized_recovery(indexed, tmp_path, command):
    repo, existing_state = indexed
    (repo / "booking.py").write_bytes(b'def broken():\n    return "\xff"\n')
    state = tmp_path / "fresh-state" if command == "index" else existing_state
    args = [sys.executable, "-c", "from codeintel.lab.entry import main; main()", "lab",
            command, "--repo", str(repo), "--state-dir", str(state)]
    if command == "query":
        args.append("reserve_seats")
    run = subprocess.run(args, capture_output=True, timeout=20)
    assert run.returncode == 2, run.stderr.decode("utf-8", errors="replace")
    assert run.stdout == b""
    error = json.loads(run.stderr)
    assert set(error) == {"status", "code", "recovery"}
    assert error["status"] == "ERROR"
    assert error["code"] == "SOURCE_INVALID_UTF8"
    assert isinstance(error["recovery"], str) and error["recovery"].strip()
    assert "utf-8" in error["recovery"].lower()
    stderr = run.stderr.decode("utf-8")
    assert "Traceback" not in stderr
    for private_path in (tmp_path, repo, state, Path(__file__).resolve().parents[1],
                         Path(sys.executable).resolve()):
        assert str(private_path) not in stderr


@pytest.mark.parametrize("command", ["index", "query"])
def test_unrelated_service_runtime_error_is_not_sanitized(indexed, monkeypatch, capsys,
                                                       command):
    from codeintel.lab.cli import main
    from codeintel.service.production import ProductionDomainService

    repo, state = indexed
    if command == "query":
        (repo / "booking.py").write_text("def reserve_seats():\n    return 42\n")
    failure = RuntimeError("unrelated programming failure")

    def broken_reindex(self):
        raise failure

    monkeypatch.setattr(ProductionDomainService, "reindex", broken_reindex)
    args = [command, "--repo", str(repo), "--state-dir", str(state)]
    if command == "query":
        args.append("reserve_seats")
    with pytest.raises(RuntimeError) as caught:
        main(args)
    assert caught.value is failure
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""
