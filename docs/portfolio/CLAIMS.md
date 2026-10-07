# Claim → reproduced evidence

Scope: an offline code-context experiment. The user-facing contract is the CLI, its literal packets and explicit failure states. This current runtime demonstrates no downstream agent integration, autonomy, adoption, production readiness or savings. Separate retired integrations have favorable and adverse historical evidence, bounded below.

Run `"$PYTHON" "$CHECKOUT/scripts/reproduce_claims.py" --output "$WORK/claims"` using the absolute paths from the [README installation](../../README.md). The script uses authored synthetic cases and the maintained source, blocks in-process socket connection primitives, records exact runtime hashes, and keeps every output. It is an auditable demonstration, not a blind quality benchmark. The [evidence index](EVIDENCE_INDEX.md) identifies historical acceptance separately; generate fresh outputs for the selected product candidate.

| ID | Exact claim | Reproduced evidence | Boundary |
|---|---|---|---|
| C01 | Locate authored Python/TS/JS symbols | Same 24 evaluation rows, both arms | Four authored cases, three repetitions; no superiority |
| C02 | Return source with relative path, literal span and SHA-256 | Packet source/file hashes and byte-span verification | Selected source only; not producer authentication |
| C03 | Enforce the whole JSON byte limit | Budgets 1024/2048/4096/8192, including metadata/newline | Bytes are not tokens, costs or agent savings |
| C04 | Reject changed selected source | Scripted edit → STALE_SOURCE → fresh query | Demo supplies the edit; no autonomous diagnosis |
| C05 | Explain missing-index recovery | INDEX_REQUIRED before source emission | Expected error contract, not general availability |
| C06 | Store repeated text once with verifiable aliases | Both packets retain the second location | Text equality is not semantic equivalence; aliases are candidate/budget-bounded |
| C07 | Inspect source without editing it; reject in-source receipt destinations | Before/after digests + CLI RECORD_INSIDE_REPOSITORY with empty stdout | Index state and optional output are external writes |
| C08 | Verify selected source rather than query completeness | Old EMPTY still verifies after a new matching file; new query finds it | Does not re-run relevance or verify the whole snapshot |
| C09 | Record opt-in metadata without source/query text | Receipt fields and null token/cost/agent metrics | Required receipt is prepared before stdout; neither proves consumer receipt |
| C10 | Rebuild a fresh complete generation on mutation | Different generation IDs; full-rebuild flag | No proven incremental speedup or scaling claim |
| C11 | Inspect actual project implementation | Authored `verify_packet` query on maintained runtime | One known-symbol example, not a retrieval benchmark |
| C12 | Compare under an equal packet contract | Same initial index, queries, serializer/budget and alternated arm order | Ranked exact symbols may expand; literal arm scans initial chunks. Not ripgrep/full-file/native-agent baseline |
| C13 | Run with in-process socket primitives blocked and no model imports | Demo/evaluation completes under those hooks | Does not establish OS/subprocess-wide network isolation or agent behavior |
| C14 | Preserve equal-text locations and expose symbol coverage | Authored alias/long-symbol use cases and adversarial tests | Known literal range only; dependencies and whole-query completeness unproven |
| H02/H03 | Recompute retained positive historical totals | Sanitized AGY/Gateway rows sum to the independently checked original usage totals | Arithmetic reproduction only; no new model run, blinded scoring or current-runtime performance conclusion |
| H01 | Preserve adverse historical result | 304,636 / 179,433 reported tokens; +69.78% | Historical one-task/two-pair exploratory result, not rerun |

Additional invariants are exercised in the retained regression suite: source/state no-follow checks, schema fail-closed behavior, cancellation/resource cleanup, FTS/source identity, transaction rollback, parsing spans and generation leases. Passing assertions demonstrate their tested cases, not a universal security or production guarantee.

## Claims deliberately not made

Agent token/cost savings, better final answers, incremental indexing speed, compiler-grade semantics, novel retrieval algorithms, autonomous bug fixing, production deployment, user adoption and end-to-end agent receipt/retention. No metric from this offline exercise supports those claims.

[Historical chronology](HISTORY.md) preserves favorable and adverse observations, post-hoc scoring limits and invalidated metrics. [Use cases](USE_CASES.md) quantify the added metadata cost.

[Versioned historical evidence and current acceptance boundaries](EVIDENCE_INDEX.md)
