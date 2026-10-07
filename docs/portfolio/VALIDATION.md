# Reproduce and validate the product

The acceptance target is CPython 3.12/Linux x86_64 with the checked-in pins.
`requirements-lab.lock` includes runtime dependencies and setuptools;
`requirements-lab-dev.lock` adds the retained pytest dependencies. These are
exact-version constraints without artifact hashes, resolver provenance or a
cross-platform lock guarantee. Windows pytest's additional dependency is not
pinned. Preserve both locks for reproduction; assess upgrades separately.

The [evidence index](EVIDENCE_INDEX.md) separates historical counts from current
acceptance. No old source/wheel receipt certifies this cleanup. Every new result
must identify the actual tested clean source, runtime hashes and collected cases.
Focused checks while editing are not a full acceptance result.

## Prepare and reproduce

Use the absolute CHECKOUT, WORK, PYTHON and CODEINTEL paths from [README](../../README.md).
Keep outputs outside the checkout and use new directories/files:

```bash
"$PYTHON" -m pip install --index-url https://pypi.org/simple -r "$CHECKOUT/requirements-lab-dev.lock"
"$PYTHON" "$CHECKOUT/scripts/reproduce_claims.py" --output "$WORK/claims"
"$PYTHON" "$CHECKOUT/scripts/reproduce_use_cases.py" --output "$WORK/use-cases.json"
"$PYTHON" "$CHECKOUT/scripts/review_retrieval_cases.py" --output "$WORK/authored-review"
"$PYTHON" "$CHECKOUT/scripts/review_retrieval_cases.py" --handoff --output "$WORK/handoff-review"
"$CODEINTEL" lab evaluate --format text
```

The evaluation is four authored cases × two arms × three repetitions, plus ten
contract checks. The literal arm uses the same indexed chunks and serializer.
Authored retrieval reviews report `MEASURED` and preserve missing literals; they
are not blind benchmarks, agent tasks or savings estimates. Historical H01–H03
reproduction checks retained arithmetic without replaying old model integrations.

## One stable source and installed-wheel acceptance

Commit the intended candidate and verify a clean checkout before running the
full gates. A gitless curated archive supports installed acceptance with a
matching passing source receipt; a standard sdist supports only its package recipe.

```bash
"$PYTHON" "$CHECKOUT/scripts/validate_portfolio.py" --output "$WORK/source-acceptance"
"$PYTHON" "$CHECKOUT/scripts/build_portfolio_snapshot.py" --output "$WORK/source-package"
python3.12 -m venv "$WORK/installed-check"
"$WORK/installed-check/bin/python" -m pip install --index-url https://pypi.org/simple -r "$CHECKOUT/requirements-lab-dev.lock"
"$PYTHON" "$CHECKOUT/scripts/verify_installed_package.py" \
  --source "$WORK/source-package/source" \
  --python "$WORK/installed-check/bin/python" \
  --source-receipt "$WORK/source-acceptance/receipt.json" \
  --output "$WORK/installed-acceptance"
```

The fresh installed environment must not already contain CodeIntel. Dependency
preparation uses PyPI; wheel build/install and lab checks need no inference provider.
The source gate independently collects pytest nodes, then executes the full suite
once in its normal monolithic shape. Execution collection and JUnit identities
must match. The installed gate checks exact delivered hashes, local Markdown
links, license metadata/notices, exact wheel runtime membership and byte parity (Python and the JS/TS fixtures),
import origins, demo and
evaluation goldens, reproduced claims, and the same collected/executed identities.
There is no fixed minimum case count: editorial migration changes the collection
honestly, without removing runtime regressions or rewriting historical receipts.

Both gates fail on skipped/failing cases, malformed or missing evidence,
collection mismatch, changed source, launch failure, nonzero exit, output
truncation, timeout or handled cancellation. Installed pytest uses isolated Python,
an external working directory and no source-pythonpath masking. Origin checks
observe each pytest process at completion, not every transient import/subprocess.

## Bounds, failures and privacy

`--suite-timeout` defaults to 360 seconds and accepts finite positive values up
to 900. Collection has 30 seconds; source/installed overall bounds are suite
seconds plus 60/300. Ordinary installed stages have 25 seconds; source Git stages
have five. These are validation deadlines, not a runtime whole-command deadline.

Preserve every failed attempt. `execution.json` records attempted stages and
observed/captured bytes; unavailable return codes are not fabricated. Command
rows contain sanitized argv and its exact-argv hash. Exclusive mode-0600
`*.command.private.json` files retain exact paths outside public exports.
Failure to create/write the output directory can still prevent a receipt.

On POSIX the subprocess owner terminates its own process group on handled
cancellation. It is not an OS sandbox and cannot promise cleanup of detached
sessions, survival of SIGKILL, or receipts after filesystem failure. Current
Windows/macOS acceptance is not claimed. Socket hooks in claim reproduction are
in-process checks, not OS/subprocess-wide isolation.

Byte hashes establish integrity, not producer authentication. Source verification
is point-in-time and selected-file only. Passing software checks establishes no
agent-quality, token/cost/time savings, universal security or production readiness.
The snapshot's high-confidence pattern scan is not a complete confidentiality
or ownership audit. Retain private history and original adverse evidence separately.
