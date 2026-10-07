"""Human-readable views of the same offline reports; no retrieval side effects."""
from __future__ import annotations


def render_demo(report: dict) -> str:
    """Narrate observed results, retaining JSON as the machine-readable default."""
    steps = report["steps"]
    old, new = steps[2]["packet"], steps[5]["packet"]
    before = next(row for row in old["selected"] if row["path"] == "booking.py")
    after = next(row for row in new["selected"] if row["path"] == "booking.py")
    start, _, end, _ = before["span"]
    lines = [
        "CodeIntel | inspect code, reject stale context",
        "Synthetic booking example. No model, account or network call.",
        "",
        f"1. Ask before indexing: {steps[0]['result']}",
        f"2. Index: {steps[1]['result']['files']} Python/TypeScript/JavaScript files; state outside source.",
        f"3. Find reserve_seats: {before['path']}:{start}-{end}",
        f"   Why: {before['reason']}",
        f"   Packet: {steps[2]['telemetry']['packet_bytes']} / {old['limits']['packet_bytes']} UTF-8 bytes, including metadata.",
        f"   Identical fragments omitted: {old['omitted']['duplicate']} (locations retained as aliases)",
        f"   Aliases: {', '.join(a['path'] for a in before['aliases']) or 'none'}; known symbol covered={str(before['coverage']['complete']).lower()}",
        "",
        *["   " + line for line in before["source"].splitlines()],
        "",
        f"4. Apply the demo's scripted edit: {steps[3]['change']}",
        "   Only the new synthetic copy is changed; CodeIntel did not diagnose this bug.",
        f"5. Verify the old packet: {steps[4]['result']}",
        f"6. Query again: refreshed={str(new['refreshed']).lower()}; current source verified.",
        f"   Now: {after['source'].splitlines()[-1].strip()}",
        f"   Source SHA-256: {before['source_sha256'][:12]} -> {after['source_sha256'][:12]} (abbreviated)",
        f"   Packet: {steps[5]['telemetry']['packet_bytes']} / {new['limits']['packet_bytes']} UTF-8 bytes.",
        f"   Selected fragments: {len(new['selected'])}; duplicate.py intentionally keeps the old behavior.",
        "",
        f"Result: {report['status']} | exact source, bounded packets, observable invalidation.",
        "This demonstrates a retrieval contract, not better agent answers or token/cost savings.",
        "Use --format json for full packets, hashes and telemetry (the default).",
    ]
    return "\n".join(lines) + "\n"


def render_evaluation(report: dict) -> str:
    """Show baseline parity and contract outcomes without implying a benchmark win."""
    lines = ["CodeIntel | offline contract evaluation", ""]
    for arm in ("ranked", "literal_scan"):
        rows = [row for row in report["rows"] if row["arm"] == arm]
        lines.append(f"{arm}: {sum(bool(row['correct']) for row in rows)} / {len(rows)} expected outcomes")
    checks = report["checks"]
    lines.extend([
        f"Contracts: {sum(bool(check['pass']) for check in checks)} / {len(checks)} passed",
        "",
        *[f"  {'PASS' if check['pass'] else 'FAIL'} {check['scenario']}" +
          (f": {check['result']} ({check['packet_bytes']} bytes)" if "packet_bytes" in check else "")
          for check in checks],
        "",
        f"Result: {report['status']} | model calls: {report['model_calls']}",
        "Both arms start from the same index, queries, serializer and budget; ranked exact symbols may expand.",
        report["limits"],
        "A matching outcome count does not establish ranking superiority or agent savings.",
        "Use --format json for individual rows and measured durations (the default).",
    ])
    return "\n".join(lines) + "\n"
