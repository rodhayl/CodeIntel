"""Build a deterministic, MIT-selected source candidate from a clean Git HEAD.

No upload/publication. An allowlist excludes history, foreign source, private
traces and experiments. Publication needs separate approval of a clean destination.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import re
import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = ("README.md", "README.es.md", "README.package.md", "MANIFEST.in", "AGENTS.md", "pyproject.toml", "requirements-lab.lock",
         "requirements-lab-dev.lock", "LICENSE", "THIRD_PARTY_NOTICES.md", "docs/QUICKSTART.md",
         "docs/reports/portfolio-readiness/public/historical-measurements.json")
# Documentation is an explicit reviewed subset. A new file in docs/portfolio
# must never become public merely through directory placement.
PRODUCT_DOCS = (
    'docs/portfolio/ALTERNATIVES.md',
    'docs/portfolio/CLAIMS.md',
    'docs/portfolio/CONTEXT_HANDOFF.md',
    'docs/portfolio/DEMO.txt',
    'docs/portfolio/DESIGN.md',
    'docs/portfolio/EVALUATION.txt',
    'docs/portfolio/EVIDENCE_INDEX.json',
    'docs/portfolio/EVIDENCE_INDEX.md',
    'docs/portfolio/HISTORICAL_EVIDENCE.json',
    'docs/portfolio/HISTORY.md',
    'docs/portfolio/LICENSING.md',
    'docs/portfolio/REAL_SOURCE_CURRENT.md',
    'docs/portfolio/RETRIEVAL_REVIEW.md',
    'docs/portfolio/SOURCE_ACCESS.md',
    'docs/portfolio/USE_CASES.md',
    'docs/portfolio/VALIDATION.md',
    'docs/portfolio/assets/README.md',
    'docs/portfolio/assets/architecture.mmd',
    'docs/portfolio/assets/architecture.svg',
    'docs/portfolio/assets/freshness.mmd',
    'docs/portfolio/assets/freshness.svg',
    'docs/portfolio/diagnostics/adversarial-known-cases.json',
    'docs/portfolio/diagnostics/context-handoff-rubric.json',
    'docs/portfolio/runtime-inventory.json',
)
PUBLIC_PREFIXES = ("codeintel/", "tests/")
PUBLIC_SCRIPTS = ("scripts/audit_runtime_scope.py", "scripts/reproduce_claims.py", "scripts/capture_demo.py",
                  "scripts/build_portfolio_snapshot.py", "scripts/validate_portfolio.py",
                  "scripts/verify_installed_package.py",
                  "scripts/reproduce_use_cases.py", "scripts/validation_contract.py",
                  "scripts/review_retrieval_cases.py")

PATTERNS = {"private_key": r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
            "github_token": r"\b(?:ghp_|github_pat_)[A-Za-z0-9_]{30,}\b",
            "openai_style_key": r"\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}\b",
            "host_path": r"/(?:root|home|Users)/[A-Za-z0-9_.-]+/"}


def audit_content(files):
    findings = []
    for name, raw in sorted(files.items()):
        if len(raw) > 2 * 1024 * 1024:
            findings.append({"path": name, "kind": "oversized"})
        text = raw.decode("utf-8", errors="replace")
        for kind, pattern in PATTERNS.items():
            if re.search(pattern, text):
                findings.append({"path": name, "kind": kind})
    return findings


def select_files(tracked):
    """Select product/review inputs; reject a checkout missing a required input."""
    tracked = set(tracked)
    required = set(FILES) | set(PRODUCT_DOCS) | set(PUBLIC_SCRIPTS)
    missing = required - tracked
    if missing:
        raise ValueError("Missing required snapshot inputs: " + ", ".join(sorted(missing)))
    return required | {name for name in tracked if name.startswith(PUBLIC_PREFIXES)}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT):
        raise SystemExit("Snapshot requires a clean HEAD")
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    tracked = subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True).splitlines()
    chosen = select_files(tracked)
    files = {name: subprocess.check_output(["git", "show", f"{sha}:{name}"], cwd=ROOT) for name in sorted(chosen)}
    # Direct installs and curated installs use byte-identical runtime/package metadata.
    transforms = []
    findings = audit_content(files)
    if findings:
        raise SystemExit(json.dumps({"status": "REVIEW_REQUIRED", "findings": findings}))
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    manifest = {"schema": "portfolio-snapshot-v2", "source_commit": sha,
                "files": {name: hashlib.sha256(raw).hexdigest() for name, raw in sorted(files.items())},
                "transformations": transforms,
                "excluded": ["Git history", "private logs and audit directories", "foreign repositories/fixtures", "gateway/hooks/GUI/handoff/evaluation/release modules", "SCIP generated schema and watcher", "personal and website editorial sources/export tooling", "historical receipts, source packets and terminal captures", "private screenshots, models, caches and build artifacts"],
                "scan": {"findings": findings, "scope": "allowlisted bytes, high-confidence patterns only; no absolute guarantee"},
                "license": "MIT", "license_resolved": True, "publication_authorized": False, "license_scope": "current offline source; no retroactive licensing of excluded private history",
                "release_allowed": False, "release_blockers": ["Final review and authorization of a new clean public destination"],
                "software_production_ready": False}
    files["SNAPSHOT_MANIFEST.json"] = (json.dumps(manifest, indent=2) + "\n").encode()
    (out / "manifest.json").write_bytes(files["SNAPSHOT_MANIFEST.json"])
    snapshot = out / "source"
    for name, raw in files.items():
        path = snapshot / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for name, raw in sorted(files.items()):
            info = tarfile.TarInfo("codeintel-portfolio/" + name)
            info.size, info.mtime, info.mode = len(raw), 0, 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            archive.addfile(info, io.BytesIO(raw))
    package = out / "codeintel-portfolio-review.tar.gz"
    with package.open("xb") as stream:
        with gzip.GzipFile(filename="", mode="wb", fileobj=stream, mtime=0) as compressed:
            compressed.write(buffer.getvalue())
    print(json.dumps({"status": "REVIEW_ONLY", "files": len(files), "bytes": package.stat().st_size,
                      "sha256": hashlib.sha256(package.read_bytes()).hexdigest(), "source_commit": sha,
                      "release_allowed": False}))


if __name__ == "__main__":
    main()
