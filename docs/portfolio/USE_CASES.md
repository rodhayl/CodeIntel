# Inspect the failure, the fix and the tradeoff

These are authored offline examples with no model calls. Frozen October 4–5 packets remain private with their runtime SHA-256 identities. Numeric examples below are dated observations, not a rerun of this product tree. Reproduce after the [README installation](../../README.md), using its absolute `PYTHON`, `CHECKOUT` and `WORK` paths:

```bash
"$PYTHON" "$CHECKOUT/scripts/reproduce_use_cases.py" --output "$WORK/use-cases.json"
"$PYTHON" -m pip install --index-url https://pypi.org/simple -r "$CHECKOUT/requirements-lab-dev.lock"
"$PYTHON" -m pytest -q "$CHECKOUT/tests/test_lab_alias_symbol_coverage.py"
```

Use a new output filename. The script creates and removes only its own disposable source/state workspace. It does not mutate the inspected checkout or access an external repository.

## 1. Same function text, different behavior

Two synthetic modules contain the same `price(amount)` body, which computes `amount * (1 + TAX)`. `gross.py` sets `TAX = .21`; `net.py` sets `TAX = 0`. The intended results at 100 are 121 and 100. The old packet deduplicated the second body and its path disappeared from the default output.

Packet v2 stores the body once and keeps the other location in `aliases`, with its own span and whole-file hash. Verification opens both locations. Changing only `net.py`'s constant produces `STALE_SOURCE`, even though the function text is unchanged. Re-querying refreshes the generation.

The packet explicitly warns that aliases share text, not imports/constants. It does not automatically deliver either constant or resolve dependencies. Reading each complete module is the next step before changing the function.

Historical recorded costs in this fixture:

- Complete v2 packet: **1,252 bytes**; one stored body: **48 bytes**; metadata: **1,204 bytes**.
- Removing just the new alias/coverage/scope fields yields a **788-byte v1-shape projection**. The new information costs **464 bytes** here. This is a serializer projection of the same output, not a rerun of the old engine or a performance comparison.
- Both complete source files total **116 bytes**. A direct reader is simpler for this tiny known case and also sees the constants. That byte count excludes its command/framing overhead, so it is not a protocol-normalized benchmark.

The 32-candidate cap means alias lists are not exhaustive. At small budgets, omitted alias provenance is counted under `omitted.budget`. `omitted.duplicate` counts repeated text represented by included aliases; it does not claim semantic equivalence or cross-query memory.

## 2. A long symbol must expose more than its prologue

The synthetic `validate` function has enough lines to span several index chunks. Its final guard raises an error near the end. An exact lookup with an 8,192-byte budget expands to the complete known function range, including that guard. The recorded packet uses **4,244 bytes**; the complete original file is **3,141 bytes**.

A deliberately larger variant cannot fit. Its 4,096-byte budget returns a **2,869-byte partial packet**, with `coverage.complete=false` and `coverage.symbol_span` identifying the requested range. The fallback is explicit: read that range, or the complete `rules.py`, using the editor or a source-reading tool. Raising the budget may help; 8,192 bytes is still a hard maximum, not a completeness promise.

The same regression suite covers nested child definitions, accented UTF-8 text, duplicated names in different files, deletion/change of each alias and malformed nested packet data. “Complete” concerns the known syntax range only. It does not mean complete imports, callers, dependencies or a correct answer to the user's task.

## 3. Inspect the implementation that enforces the contract

[The real-source walkthrough](REAL_SOURCE_CURRENT.md) queries `verify_packet` and the longer `_validate_packet_schema` in CodeIntel itself, then verifies the output. This demonstrates a practical lookup beyond the fixture while keeping its authored-query limitation explicit.

## Español

Los dos fallos se reproducen con datos sintéticos: cuerpos idénticos que dependen de impuestos distintos y una función larga cuya guarda final quedaba fuera. Ahora se conservan alias verificables y cobertura del rango conocido. Si falta contexto, se indica leer el rango o el archivo. Los metadatos tienen coste: 464 bytes adicionales en el ejemplo de alias. Leer los dos archivos completos ocupa sólo 116 bytes y es una alternativa mejor para ese caso pequeño. Las pruebas no demuestran ahorro de agente ni resolución semántica de dependencias.
