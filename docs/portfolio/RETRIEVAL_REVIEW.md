# Authored retrieval review: useful context and deliberate limits

[Design](DESIGN.md) · [Equal-budget diagnostic](CONTEXT_HANDOFF.md)

This diagnostic review is separate from the four-case `codeintel lab evaluate` demo. It fixes eight questions and their required source literals over ten new synthetic Python/TypeScript files before indexing. Every result is retained. It does not evaluate an agent answer, establish statistical superiority, or measure token/cost savings.

## What is compared

- **Ranked:** the maintained lexical/exact-symbol/heuristic-neighbor path, including known-symbol expansion.
- **Literal scan:** substring matching over the same indexed chunks and the same packet serializer. It shares the parser and does not implement qualified-symbol lookup or full-symbol expansion. This comparison therefore does not isolate ranking alone.
- **Direct reading:** whole files whose paths are supplied by the rubric. This is an oracle-assisted alternative, not a competing search engine: file discovery and message framing are excluded from its byte count.

There is one pass, alternating the arm order by question. The questions are authored diagnostics, not random tasks or a blind holdout. The rubric hash, source identity, runtime hashes, every emitted packet, missing literals and byte counts are recorded by each new run. Historical October 5 outputs are preserved privately and are not current acceptance. Matching declared text is a narrower property than understanding or solving the task. The absence control is one of the eight questions; it is not a retrieved implementation.

## Cases that make the boundary visible

| Question | What the reviewer must inspect |
|---|---|
| A `file_` path and a misleading comment | Whether the implementation is returned rather than its mention |
| Two functions named `parse` | Qualified-symbol behavior versus literal substring matching |
| A typed TypeScript arrow and its callee | Whether both required code ranges appear |
| Equal function bodies with different tax constants | Whether both constants, not just the shared body and its aliases, are present |
| An imported timeout constant | Whether retrieval also includes the value in the other file |
| A long function, small budget | Whether the final guard is omitted and the result remains explicitly incomplete |
| The same function, larger budget | What full-symbol expansion changes and what it costs in serialized bytes |
| An absent symbol | Whether the known synthetic corpus produces an explicit empty result |

The tax constants, imported dependency and small-budget final guard expose missing context in the measured ranked result. A verified packet can still be insufficient to answer the question. The wider-budget guard case demonstrates one bounded recovery route; it does not make dependencies complete. Do not tune these cases after inspecting their results and then call them independent evaluation.

The direct-reading rows retain inconvenient comparisons: these files are small, while paths, aliases, ranges, hashes and the rest of the JSON consume bytes. Use this lab when inspecting those contracts matters. For a small known file, reading it directly can be simpler. No conversion from these bytes to tokens, latency or price is valid.

## Reproduce

Use the absolute `PYTHON`, `CHECKOUT` and `WORK` paths from the [README installation](../../README.md):

```bash
"$PYTHON" "$CHECKOUT/scripts/review_retrieval_cases.py" --output "$WORK/authored-review"
```

Choose a new directory outside the checkout. The command writes the rubric before indexing and reports `MEASURED`, not a blanket `PASS`. A missing required literal remains a result in the receipt. A clean-source identity is required for the published run; a dirty development probe is not final acceptance. Software test acceptance remains separately recorded in [VALIDATION.md](VALIDATION.md).
