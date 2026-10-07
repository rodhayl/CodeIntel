# CodeIntel package: offline CLI build source

CodeIntel is an experimental offline code-context lab. It indexes local
source and returns byte-budgeted literal packets with provenance and point-in-time
verification of selected files. The maintained interface is
`codeintel lab {index,query,verify,demo,evaluate}`. No model, API key or GPU is needed.
No improvement in agent answers, tokens, cost or human time is demonstrated.

## Artifact purposes

- The wheel is the installed CLI runtime, including four synthetic demo fixtures.
- The standard sdist is **build-source**, **not the review archive**. It includes
  the runtime, build metadata, runtime/build lock and this installation recipe.
  It intentionally contains no tests, maintenance scripts, portfolio documents,
  historical evidence or Git history. Building or running it does not establish
  full-source acceptance or authenticate the source's original Git identity.
- The separately curated history-free review archive contains selected tests,
  documentation and review tools. Authorized reviewers use a clean selected Git
  checkout for the source gate and that curated archive for installed acceptance.
  The source gate requires Git; a gitless artifact cannot claim to have run it.

Access to this package must already be authorized. No public package registry,
public download, release or production readiness is implied by this README.

## Install from an extracted standard sdist

Requires Python 3.12; Linux x86_64 is the validated platform. PyPI access is needed
for the pinned dependency installation. The lock is an exact-version constraint
set for that target, without artifact hashes or a cross-platform guarantee.
Setuptools is intentionally included for builds without isolation. Keep the pins
for reproduction; upgrades require separate compatibility review and acceptance.
Start in the extracted sdist directory:

```bash
SOURCE="$(pwd -P)"
WORK="$(mktemp -d /tmp/codeintel-package.XXXXXX)"
python3.12 -m venv "$WORK/venv"
PYTHON="$WORK/venv/bin/python"
CODEINTEL="$WORK/venv/bin/codeintel"
"$PYTHON" -m pip install --index-url https://pypi.org/simple -r "$SOURCE/requirements-lab.lock"
"$PYTHON" -m pip install --no-index --no-deps --no-build-isolation "$SOURCE"
cd "$WORK"
"$PYTHON" -I -c 'import codeintel; print(codeintel.__file__)'
"$CODEINTEL" lab demo --workspace "$WORK/demo" --format text
"$CODEINTEL" lab evaluate --format text
```

Choose a new demo workspace. Its edit is scripted; CodeIntel does not discover or
repair the bug. These exercises are contract checks, not an agent evaluation.
After installation the CLI runs offline. See `codeintel lab --help` and the
individual command's `--help` for options. Source/state/receipt paths should be
absolute; derived state and optional receipts belong outside the source repository.

Always check the process exit status. A prepared receipt does not confirm stdout
delivery. On output failure, partial bytes may have escaped and a prepared receipt
may remain. Verification does not prove relevance, authenticity, full dependencies
or the continued completeness of an old empty result. Changes rebuild the complete
admitted snapshot; no incremental performance or autonomous integration is claimed.

The current offline source is MIT licensed; see LICENSE and THIRD_PARTY_NOTICES.md
in this archive (or the installed wheel's license metadata). Private history and
third-party components are not retroactively relicensed.
