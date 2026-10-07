# Context selection and the reading handoff

CodeIntel's bounded packet is a literal-evidence contract. It does not establish that the context is sufficient to answer a question. `VERIFIED` is not an answer-quality score.

## Retained audit counterexamples

The [independent October 5 audit](https://github.com/rodhayl/trialsMemoryAgent/blob/a143367b3caac8e507af3aace498a0b7d1b43fe4/docs/reports/adversarial-audit-20261005/REPORT.md) measured ranked **6/11** and literal discovery plus complete-file reading **8/11** on its new synthetic corpus. The latter had **no equivalent byte cap**. Those figures are not a fair-budget superiority result. On CodeIntel's own source it measured 3/5 versus 4/5 under the same unequal conditions. All ranked packets verified, including insufficient ones.

The [original questions and sources](diagnostics/adversarial-known-cases.json) retain SHA-256 `7faf543d0d4549c62df766b97a4d809acc456d03bdca8498a22311d4840640f1`. They are now known regression cases, not a new holdout. Missing module constants, imported values, short literals and a small-budget guard are useful negative examples. No historical result is overwritten by repairs or a later rerun.

## A small operational transition

- `lab query --literal` exposes the existing same-index literal-chunk selection policy, with the same packet and verification contract. It does not become a whole-file search engine.
- Use `coverage.symbol_span` to read an incomplete syntax range in an editor; inspect the whole file when module constants or imports matter.
- Follow actual imports or identifier definitions with ordinary literal search and read the defining files. Neither `coverage.complete` nor source verification proves dependency completeness.
- For any combined workflow, account for the initial packet plus the additional discovery and reading. A tiny known file may be cheaper to read directly; an arbitrary large file may not fit.

This reuses the existing CLI and ordinary reading. There is no new embeddings system, inference provider, router, autonomous agent or dependency resolver.

## Prospectively fixed, equal-output-budget diagnostic

The [new rubric](diagnostics/context-handoff-rubric.json) was frozen before indexing or querying its new corpus: SHA-256 `7a69fa57d554dbd7228e663b3cab649249efd9c0b2d58a9d2a6b2b6b51c0c966`. Eight authored questions cover seven substantive code inspections and one separate literal-absence control. They use eight small Python/TypeScript sources. These are held out from result inspection, but task families were informed by the audit. They are not blinded, representative, statistically independent or agent tasks.

The three arms receive identical source and query, with no oracle paths or query rewriting:

1. Ranked literal packet.
2. Existing literal-chunk packet.
3. Independent literal discovery over whole files followed by reading matching files in path order.

All three share each question's complete UTF-8 output cap, including newline, source, paths and metadata. The third arm also includes its discovery path/hash list. It admits whole files only, with the same 32-candidate and five-selection caps. A file that does not fit is omitted, rather than cropped around an expected answer. It uses the same conservative 64-byte final-accounting reservation. It is not ripgrep and does not claim the packet's provenance verification contract.

The rubric measures declared literals at declared locations. It does not measure generated-answer correctness. Output normalization does not equalize algorithms, indexing, CPU or integrity guarantees. Initial indexing time and corpus bytes are reported separately; rotating order and one run per arm do not support a speed claim. No provider/model calls are made. No token, price or savings conversion is valid.

Reproduce on a stable candidate using the absolute paths from [README installation](../../README.md), with a new output directory outside the checkout:

```bash
"$PYTHON" "$CHECKOUT/scripts/review_retrieval_cases.py" --handoff --output "$WORK/handoff-review"
```

Every outcome, packet and missing literal is kept in the result. No ranking or rubric is tuned after viewing it. Fresh output records its own measured outcome and source identity; the historical summary below is not a current rerun.

## Recorded result, including failures

The October 5 private frozen results and packets record **4/8 ranked**, **3/8 literal chunks**, and **5/8 literal files**. Excluding the shared absence control, the figures are **3/7**, **2/7**, and **4/7**. The file reader supplies module constants in the duplicate-body example; ranked selection supplies the typed function and callee. All three miss the imported retry value and the guard under the small budget. A qualified-name query still fails to supply the required module constant. These results do not establish a generally better retrieval method.

The first measurement was made from the repair working tree before acceptance, with `source_dirty: true` and every runtime hash recorded. Its baseline commit field is not a claim to have tested the unchanged baseline. Final acceptance compares those runtime bytes to its exact clean candidate; the measurement is not silently relabelled as a clean acceptance run. No ranking/corpus/rubric tuning followed result inspection.

The known October 5 audit-case rerun records ranked **8/11** on the original synthetic questions after `id` and `雪山` began matching; its literal-chunk result remains **6/11**. Self-source ranked and literal chunks remain **3/5**. The original unbounded whole-file control was not rerun by this script and stays an unequal-budget historical comparison. Source edits still refresh the query and remove the old guard. These are known regression outcomes, not held-out wins.
