# CodeIntel · offline code-context lab

[Español](README.es.md) · [Quickstart](docs/QUICKSTART.md) · [Design](docs/portfolio/DESIGN.md) · [Validation](docs/portfolio/VALIDATION.md)

CodeIntel indexes local source and returns exact code with paths, literal spans, hashes and a complete serialized-byte budget. Verification rejects changes to selected files. The maintained interface is `codeintel lab {index,query,verify,demo,evaluate}`. Python, tree-sitter and SQLite/FTS5; no model, API key or GPU.

## Install and try it

Start in a clean checkout of [this history-free repository](https://github.com/rodhayl/CodeIntel) or its matching reviewed source archive. [Source identity and access](docs/portfolio/SOURCE_ACCESS.md#get-the-identified-source) explain how to verify the chosen version. Access to the original private development repository is not required to install these files. Historical acceptance does not certify later edits.

The validated target is **CPython 3.12 on Linux x86_64**. The two checked-in lock files are exact-version constraints for that target, without artifact hashes or a cross-platform guarantee. Keep these pins for reproduction; dependency upgrades need separate compatibility review and fresh acceptance. Setuptools is intentionally pinned for builds without isolation. Dependency preparation uses PyPI; the installed CLI runs offline.

```bash
CHECKOUT="$(pwd -P)"
WORK="$(mktemp -d /tmp/codeintel-review.XXXXXX)"
python3.12 -m venv "$WORK/venv"
PYTHON="$WORK/venv/bin/python"
CODEINTEL="$WORK/venv/bin/codeintel"
"$PYTHON" -m pip install --index-url https://pypi.org/simple -r "$CHECKOUT/requirements-lab.lock"
"$PYTHON" -m pip install --no-index --no-deps --no-build-isolation "$CHECKOUT"
cd "$WORK"
"$PYTHON" -I -c 'import codeintel; print(codeintel.__file__)'
"$CODEINTEL" lab demo --workspace "$WORK/demo" --format text
"$CODEINTEL" lab evaluate --format text
```

The demo creates four synthetic files in a new directory, queries `reserve_seats`, makes a scripted edit, rejects the stale packet and queries again. It never discovers or fixes a bug autonomously. The fixed evaluation has four authored cases, two arms and three repetitions, plus ten contract checks. Its literal control scans the same indexed chunks; it is not an independent whole-file or agent baseline.

[Demo output](docs/portfolio/DEMO.txt) · [Evaluation output](docs/portfolio/EVALUATION.txt) · [Inspect your repository](docs/QUICKSTART.md) · [Inspect CodeIntel](docs/portfolio/REAL_SOURCE_CURRENT.md)

## Contracts and limits

- Packets contain exact source without automatic secret redaction. Treat them as private as the inspected repository; state and optional receipts belong outside it.
- The entire JSON plus newline fits 1,024–8,192 bytes, 4,096 by default. Equal fragments share storage with verifiable aliases. Exact-symbol expansion uses a conservative 64-byte reserve; a symbol can stay partial even when a tighter serialization would fit.
- Verification checks selected files at that moment. It does not authenticate the producer, re-run relevance, prove complete dependencies or establish that an old `EMPTY` remains complete. Read the recorded range and defining files when context is insufficient.
- Python, JavaScript and TypeScript have structural parsing. Other admitted UTF-8 text uses fallback chunks without equivalent language semantics. Changes rebuild the complete admitted snapshot.
- No agent integration, providers, dense retrieval, compiler/SCIP runner or GUI is shipped. There is no demonstrated current agent-quality, token, cost or time benefit. [Historical limitations](docs/portfolio/HISTORY.md) preserve favorable and adverse results from different retired systems.

![Offline architecture](docs/portfolio/assets/architecture.svg)

[Detailed contracts and compatibility](docs/portfolio/DESIGN.md) · [Reproducible claims](docs/portfolio/CLAIMS.md) · [Known failure cases](docs/portfolio/CONTEXT_HANDOFF.md) · [Runtime inventory](docs/portfolio/runtime-inventory.json)

## Development and distribution

Install `requirements-lab-dev.lock` into a venv, then install the project with `--no-deps --no-build-isolation`. Use `python -m pytest tests` for the retained suite. Acceptance additionally checks a clean source, independent test collection, installed-wheel parity and exact runtime bytes; use the [complete validation procedure](docs/portfolio/VALIDATION.md).

The wheel contains the runtime and four synthetic fixtures. The standard sdist is smaller build source with its own [package recipe](README.package.md), without review tests or tools. The curated review archive adds only explicitly selected technical docs and retained tests/tools. Editorial sources, personal material and historical receipts are kept separately and are not product dependencies.

The current source is [MIT licensed](LICENSE), with [dependency notices](THIRD_PARTY_NOTICES.md). The original `rodhayl/CodeIntelPriv` development repository and its historical Git objects remain private and are not included or retroactively relicensed here. A history-free candidate does not imply publication, a registry upload or production readiness. [License scope](docs/portfolio/LICENSING.md).
