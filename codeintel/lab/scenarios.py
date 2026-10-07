"""Synthetic demo and fixed offline comparisons; no agent calls."""
from __future__ import annotations

import shutil
import tempfile
import time
from pathlib import Path

from codeintel import __version__
from codeintel.lab.retrieval import (LabError, index_repository, query_repository,
                                     serialize, verify_packet)

FIXTURE = Path(__file__).with_name("fixture")


def run_demo(workspace: Path) -> dict:
    """Only mutate the synthetic copy inside a new directory owned by this run."""
    workspace.mkdir(parents=True, exist_ok=False)
    repo, state = workspace / "repository", workspace / "state"
    shutil.copytree(FIXTURE, repo, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    steps = []
    try:
        query_repository(repo, state, "reserve_seats")
    except LabError as exc:
        if exc.code != "INDEX_REQUIRED":
            raise
        steps.append({"step": "query_before_index", "result": exc.code, "recovery": exc.recovery})
    else:
        raise AssertionError("An unindexed query must be rejected")
    index, audit = index_repository(repo, state)
    steps.append({"step": "index", "result": index, "telemetry": audit})
    old, audit = query_repository(repo, state, "reserve_seats")
    verify_packet(repo, old)
    if not old["selected"] or not old["omitted"]["duplicate"]:
        raise AssertionError("Expected relevant source and an omitted duplicate")
    steps.append({"step": "query", "packet": old, "telemetry": audit})
    path = repo / "booking.py"
    path.write_bytes(path.read_bytes().replace(b"return available + requested", b"return available - requested"))
    steps.append({"step": "modify", "path": "booking.py", "change": "available + requested -> available - requested"})
    try:
        verify_packet(repo, old)
    except LabError as exc:
        if exc.code != "STALE_SOURCE":
            raise
        steps.append({"step": "verify_old_packet", "result": exc.code, "recovery": exc.recovery})
    else:
        raise AssertionError("Changed source must invalidate the old packet")
    new, audit = query_repository(repo, state, "reserve_seats")
    verify_packet(repo, new)
    if not new["refreshed"] or new["snapshot_sha256"] == old["snapshot_sha256"]:
        raise AssertionError("A mutation must create a fresh snapshot")
    if not any(r["path"] == "booking.py" and "available - requested" in r["source"] for r in new["selected"]):
        raise AssertionError("The new packet must contain the repaired source")
    steps.append({"step": "query_after_modify", "packet": new, "telemetry": audit})
    return {"schema": "codeintel-lab-demo-v1", "version": __version__, "status": "PASS",
            "steps": steps, "scope": "synthetic copy only; prepared packets, no agent receipt"}


def evaluate() -> dict:
    """Same indexed chunks, queries, renderer, byte budget and limit in both arms.

    Query duration includes service open/close, freshness and provenance checks.
    This tiny fixture is a contract experiment, not a retrieval benchmark at scale.
    """
    rows, checks = [], []
    with tempfile.TemporaryDirectory(prefix="codeintel-lab-eval-") as directory:
        root = Path(directory)
        repo, state = root / "repository", root / "state"
        shutil.copytree(FIXTURE, repo, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        index_started = time.perf_counter()
        _, index_audit = index_repository(repo, state)
        index_audit["wall_seconds_including_close"] = time.perf_counter() - index_started
        # A frozen independent oracle: require path and behavior, not score equality.
        cases = [("reserve_seats", "booking.py", "insufficient inventory"),
                 ("invoiceTotal", "billing.ts", "price * count"),
                 ("formatTicket", "display.js", "Ticket:"),
                 ("no_such_symbol_012345", None, None)]
        for repetition in range(3):
            for query, expected_path, expected_text in cases:
                # Alternate order; shared indexing cost is reported separately.
                for baseline in ([False, True] if repetition % 2 == 0 else [True, False]):
                    start = time.perf_counter()
                    packet, audit = query_repository(repo, state, query, baseline=baseline)
                    duration = time.perf_counter() - start
                    correct = (any(r["path"] == expected_path and expected_text in r["source"]
                                   for r in packet["selected"]) if expected_path else not packet["selected"])
                    verify_packet(repo, packet)
                    rows.append({"scenario": query, "repetition": repetition,
                                 "arm": "literal_scan" if baseline else "ranked", "correct": correct,
                                 "duration_seconds": duration, "status": packet["status"],
                                 "telemetry": audit})
        packet, _ = query_repository(repo, state, "reserve_seats")
        checks.append({"scenario": "duplicates", "pass": packet["omitted"]["duplicate"] > 0})
        for size in (1024, 2048, 4096, 8192):
            bounded, _ = query_repository(repo, state, "reserve_seats", max_bytes=size)
            checks.append({"scenario": f"budget_{size}", "pass": len(serialize(bounded)) <= size,
                           "result": bounded["status"], "packet_bytes": len(serialize(bounded))})
        (repo / "booking.py").write_text("def reserve_seats(available, requested):\n    return available - requested\n")
        try:
            verify_packet(repo, packet)
        except LabError as exc:
            checks.append({"scenario": "stale_packet", "pass": exc.code == "STALE_SOURCE"})
        else:
            checks.append({"scenario": "stale_packet", "pass": False})
        fresh, _ = query_repository(repo, state, "reserve_seats")
        checks.append({"scenario": "invalidation", "pass": fresh["refreshed"] and
                       any(r["path"] == "booking.py" and "available - requested" in r["source"] for r in fresh["selected"])})
        # Deletion and addition also invalidate the immutable generation.
        (repo / "billing.ts").unlink()
        deleted, _ = query_repository(repo, state, "invoiceTotal")
        checks.append({"scenario": "delete", "pass": deleted["refreshed"] and not deleted["selected"]})
        (repo / "new.py").write_text("def recover_inventory():\n    return 42\n")
        added, _ = query_repository(repo, state, "recover_inventory")
        checks.append({"scenario": "add", "pass": added["refreshed"] and bool(added["selected"])})
        try:
            query_repository(repo, state, "reserve_seats", max_bytes=0)
        except LabError as exc:
            checks.append({"scenario": "invalid_budget", "pass": exc.code == "INVALID_BUDGET"})
        else:
            checks.append({"scenario": "invalid_budget", "pass": False})
    passed = all(row["correct"] for row in rows) and all(c["pass"] for c in checks)
    return {"schema": "codeintel-lab-evaluation-v1", "version": __version__,
            "status": "PASS" if passed else "FAIL", "index": index_audit,
            "configuration": {"max_bytes": 4096, "limit": 5, "repetitions": 3, "dense": "off", "scip": False},
            "rows": rows, "checks": checks, "model_calls": 0, "agent_savings": None,
            "limits": "Four authored cases, no blind holdout, no generalization or token conversion."}
