# Technical diagrams

`architecture.svg` and `freshness.svg` are source-native diagrams with editable
Mermaid counterparts. They describe the implemented offline boundary and scripted
stale-packet sequence, not measured agent performance. The consumer/agent boundary
is unimplemented and unmeasured.

Current synthetic CLI text is retained in [DEMO.txt](../DEMO.txt) and
[EVALUATION.txt](../EVALUATION.txt) for installed verification. For new captures,
use `"$PYTHON" "$CHECKOUT/scripts/capture_demo.py" --output "$WORK/capture"`
with the absolute paths from the [README installation](../../../README.md)
and an identified clean checkout. Its optional HTML is a literal transcript viewer.

Historical terminal captures and bilingual website diagrams remain with their
separate private editorial sources, original hashes and exporter safety tests.
They are not silently refreshed, included recursively or treated as current
runtime output. No generated screenshot or invented result substitutes for a
real capture.
