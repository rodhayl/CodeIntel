# Versioned evidence

Updated October 6, 2026. The [machine-readable index](EVIDENCE_INDEX.json) retains
historical identities and original receipt hashes. Counts belong to the named
source only; two source/wheel executions are the same software cases, not twice
as many independent tasks.

The cleanup starts from remote `1e66f05a59574ce05b2c2b6de280f8c53a79a340`,
tree `595dcc95fc631cb9837e9c927612c47282abf756`. That is the pre-cleanup baseline,
not acceptance of edited product source. Current acceptance requires fresh
exact-candidate source and installed receipts using [the validation procedure](VALIDATION.md).
A local reconstruction or historical result must never be labelled current remote acceptance.

## Historical software results

- `0af24264e0203571b2be1eba1301407808241a76`: 1,236 matching source/wheel
  cases, 46 Python runtime files; maintainer acceptance after re-audit fixes.
- `1d69a6126859ae83952919d164c06a5be306d2aa`: 1,218 matching cases and
  46 runtime files; independently reproduced follow-up. This audit found
  defects beyond its green suite and did not verify the original 2a receipts.
- `2a142a73c2e53f125a338a4a48d7f9c56ae908b3`: 1,218 matching cases,
  maintainer software acceptance, not an independent value study.
- `e74cb1f0a6e5b48eba8f68c223c55f5f9446c833`: 1,198 matching cases and
  46 runtime files; earlier independently audited baseline.
- `b6c417fd3595256f8a89c4bfecf404bd191aa9bc`: 776 matching cases and
  45 Python runtime files. Its receipts are not receipts for any later count.

All above results are historical. Original receipts, failed attempts, prior
inventories, source packets and detailed chronology remain privately preserved
with their original bytes; none has been rewritten to certify this cleanup.
The original 106-case history retains 104 PASS/2 FAIL; the later 628-pass/8-skip
consolidation and original 90-second source-gate failures are also preserved.
A manually partitioned run is not the official monolithic installed acceptance.

[Current runtime byte inventory](runtime-inventory.json), live demo/evaluation
goldens and synthetic diagnostic inputs remain in product source. The sanitized
[historical ledger](HISTORICAL_EVIDENCE.json) and its [limitations](HISTORY.md)
retain both favorable and adverse experimental directions. No current agent
quality, token, cost, human-time benefit or production readiness is established.
Integrity hashes are not producer authentication.
