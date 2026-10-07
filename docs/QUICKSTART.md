# Recorrido manual offline

Primero selecciona la entrega autorizada y comprueba su identidad siguiendo [README](../README.es.md) y [acceso a fuentes](portfolio/SOURCE_ACCESS.md). Los recibos históricos no certifican cambios posteriores. Tras la instalación regular, conserva las variables absolutas CHECKOUT, WORK y CODEINTEL del README. Este recorrido se ejecuta **fuera del checkout**, sin activación ni PYTHONPATH:

```bash
cd "$WORK"
mkdir "$WORK/manual"
# Copy only the four synthetic source files, never a cache or prior index.
mkdir "$WORK/manual/repository"
cp "$CHECKOUT/codeintel/lab/fixture/booking.py" "$CHECKOUT/codeintel/lab/fixture/duplicate.py" "$CHECKOUT/codeintel/lab/fixture/billing.ts" "$CHECKOUT/codeintel/lab/fixture/display.js" "$WORK/manual/repository/"
"$CODEINTEL" lab index --repo "$WORK/manual/repository" --state-dir "$WORK/manual/state"
"$CODEINTEL" lab query --repo "$WORK/manual/repository" --state-dir "$WORK/manual/state" reserve_seats --max-bytes 4096 --record "$WORK/manual/query-01.receipt.json" > "$WORK/manual/old.json"
"$CODEINTEL" lab verify --repo "$WORK/manual/repository" --packet "$WORK/manual/old.json"
```

En un editor, cambia `return available + requested` por `return available - requested` sólo en la copia `booking.py`. Vuelve a ejecutar `verify`: sale 2 con `STALE_SOURCE` y recomienda una consulta nueva. Repite `query` con las mismas rutas y un nombre de recibo nuevo. La barrera comprueba hashes, reindexa y devuelve `refreshed=true`; `verify` acepta el nuevo paquete. La copia `duplicate.py` conserva el bug a propósito.

Resultados esperados: cuatro archivos indexados; primera selección `booking.py`, rango `[4,0,10,32]`, razón con `exact_short_name`, un duplicado omitido; todos los hashes coinciden con los literales. El rango usa columnas en bytes y fin exclusivo. `--max-bytes 1024` omite la función completa y devuelve `BUDGET_EXHAUSTED`; subirlo a 4096 la recupera. `no_such_symbol_012345` devuelve `EMPTY`. Un índice inexistente devuelve `INDEX_REQUIRED` antes de emitir fuente.

No pongas estado dentro del repositorio: se rechaza. Fuente que desaparece o cambia durante verificación produce error sin paquete; detén las escrituras y vuelve a consultar. Ante almacenamiento corrupto elige un estado nuevo y vuelve a indexar. No sobrescribimos workspaces ni recibos. Para el mismo recorrido automático: `codeintel lab demo --workspace <carpeta-nueva>`. Telemetría y ejemplos usan sólo datos sintéticos; la consulta ordinaria no escribe recibos salvo `--record`.

## Inspect your own repository (English)

After the synthetic demo, use the same read-only flow on a local Python, TypeScript or JavaScript repository you are authorized to inspect. Replace `/absolute/path/to/repository` and `my_function` below. Use a new state directory outside the repository; do not share packets from private code publicly.

```bash
"$CODEINTEL" lab index --repo /absolute/path/to/repository --state-dir /tmp/codeintel-myrepo-state
"$CODEINTEL" lab query --repo /absolute/path/to/repository --state-dir /tmp/codeintel-myrepo-state my_function --max-bytes 4096 > /tmp/codeintel-myrepo-packet.json
"$CODEINTEL" lab verify --repo /absolute/path/to/repository --packet /tmp/codeintel-myrepo-packet.json
```

The index/query commands read source and write only the chosen external state (plus an optional `--record` receipt). Ordinary queries never edit your repository or call a model. Packet output contains exact selected source without automatic secret redaction, so keep it as private as the repository. Receipt paths inside the inspected repository or through symlink parents are rejected before indexing/querying or output. Verification checks the selected files/literals only; it does not re-run relevance or prove that an `EMPTY` answer is still complete after a repository change. Query an exact symbol first; an empty answer does not prove a symbol is absent from every unsupported language or parser construct.

For a compact narration of the authored fixture use `codeintel lab demo --workspace <new-directory> --format text`; for its fixed comparison use `codeintel lab evaluate --format text`. Omit `--format text` to keep the complete JSON. The demo edits only its newly created synthetic copy.

## Alias y cobertura en el paquete v2

Un cuerpo compartido conserva sus otras ubicaciones en aliases. Verificar comprueba también esos archivos: cambiar una constante de un alias invalida el paquete aunque el cuerpo sea igual. Las listas no son exhaustivas fuera de los 32 candidatos. Si una ubicación no cabe, se cuenta como omisión por presupuesto.

coverage.symbol_span es el rango sintáctico conocido y coverage.complete indica si los literales seleccionados lo cubren. No promete imports, llamadas ni dependencias. Si es false, lee ese rango o el archivo completo con tu editor; aumentar max-bytes hasta 8192 puede ayudar, sin garantizar que todo quepa. Igualdad de texto no es igualdad de comportamiento. [Ejemplos y costes medidos](portfolio/USE_CASES.md).

## Cuando el paquete no basta / when the packet is insufficient

1. Start with the literal symbol name. For an independent selection policy over the same indexed chunks, add `--literal` to `lab query`. It performs a casefolded substring scan and reuses the existing byte-budget, source verification and freshness checks; it is not ripgrep or a semantic search.
2. Inspect every selected path and alias. If `coverage.complete` is false, use the recorded `coverage.symbol_span` in your editor to read that entire range, or open the whole file. If it is true, still inspect imports and module constants needed by the question. Coverage only describes a syntax range.
3. Follow the actual import or identifier into its defining file using your editor or ordinary literal search. An empty packet or a verified snippet is not a reason to skip this reading. This manual transition does not run an agent or resolve dependencies automatically. Count the initial packet plus every subsequent search/read when comparing workflows.

For example, `codeintel lab query --repo <source> --state-dir <external-state> --literal shipment_total --max-bytes 4096` uses the existing literal-chunk control directly. Replace the placeholder paths and query with your own. Whole-file reading often costs less for a known tiny file, while large files can exceed the packet cap. The [fixed equal-output-budget diagnostic](portfolio/CONTEXT_HANDOFF.md) preserves both outcomes.

Default ranked search uses trigram FTS for word terms of at least three characters. If all extracted word terms are only one or two characters, it separately visits at most 4,096 indexed chunks and compares at most 4 MiB of fetched content for casefolded matches of up to 64 extracted short word terms (OR). Mixed long/short queries retain the trigram path for the long terms. SQLite metadata work and physical I/O are not bounded by that byte count. Those bounds can exclude a present literal. This does not make FTS support bigrams, make punctuation-only queries arbitrary substring searches, or prove absence. `--literal` is a different, explicit same-index scan policy.

## Required receipts and stdout

With `--record`, index/query completes the exclusive receipt write, flush, fsync and close before emitting result bytes. Failure during that required write returns exit 2 and no success packet on stdout. A partially written receipt can remain after a write failure and is never overwritten; choose a new receipt path after inspecting the failure.

Result-write/flush failures return exit 2 with stdout-specific recovery advice. The CLI redirects the failed output descriptor before shutdown so Python does not retry buffered bytes, print a second finalizer error or replace that exit with 120 in the tested `/dev/full` and closed-pipe cases. Buffered bytes are also discarded after interruption during result delivery; bytes already sent cannot be recalled. This cleanup is best-effort if the output descriptor or a spare descriptor is unavailable.

The receipt labels delivery `prepared_for_stdout`: a filesystem receipt and stdout are not an atomic transaction. The receipt may exist if stdout later fails, and neither proves a consumer received or used the packet. Check the command's exit status as well as its JSON. Without `--record`, no receipt is written. Existing no-overwrite and no-follow parent checks still apply.

## Cancellation and rejected paths

Ctrl-C returns a structured `CANCELLED` error and exit 130, without a traceback. If interrupted before output, no success packet is emitted. A cancellation during output may leave partial bytes; never consume a command's result without checking its exit status. State or a partial/prepared receipt can remain: inspect it and use a new receipt path. Cancellation during the first schema initialization can leave incomplete state: reuse then fails closed with `DerivedStateValidationError`/exit 2 and no success output. Preserve that state and choose a fresh external state directory. Reindex/query recovery of an already-established state was observed separately; recovery is not promised at every interruption point. Cancellation is not rollback or proof of delivery. SIGTERM keeps the operating system's usual signal behavior.

Literal backslashes in source filenames are rejected with `SOURCE_UNAVAILABLE`/exit 2 and specific renaming advice; they are not normalized into another path. Symlink/non-regular/unreadable inputs retain their separate safe-source advice. Both failures emit no success JSON and do not overwrite existing receipts. No path details or source text are printed in recovery messages.
