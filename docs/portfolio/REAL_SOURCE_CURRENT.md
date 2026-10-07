# Inspect the maintained runtime

The [current inventory](runtime-inventory.json) records the retained runtime's
paths and exact bytes. Counts describe source files, not independent features.
Generate packets from your selected source rather than borrowing historical
spans, sizes or acceptance counts.

After the regular README installation, use absolute paths outside the checkout
and fresh state/receipt names. These are authored lookups of known implementation
symbols, not a blind benchmark:

```bash
cd "$WORK"
"$CODEINTEL" lab index --repo "$CHECKOUT/codeintel" --state-dir "$WORK/self-state"
"$CODEINTEL" lab query --repo "$CHECKOUT/codeintel" --state-dir "$WORK/self-state" verify_packet --max-bytes 4096 --limit 1 --record "$WORK/self.receipt.json" > "$WORK/self.packet.json"
"$CODEINTEL" lab verify --repo "$CHECKOUT/codeintel" --packet "$WORK/self.packet.json"
"$CODEINTEL" lab query --repo "$CHECKOUT/codeintel" --state-dir "$WORK/self-state" _validate_packet_schema --max-bytes 8192 --limit 1 > "$WORK/schema.packet.json"
"$CODEINTEL" lab verify --repo "$CHECKOUT/codeintel" --packet "$WORK/schema.packet.json"
```

Inspect span, coverage, full JSON bytes and exact file hashes. Full known-symbol
coverage excludes imports, constants, callees and task sufficiency: read the
defining files too. Verification checks selected files at that moment, not
ranking, an old empty result's ongoing completeness or producer authenticity.
Packets contain real source and inherit its privacy. Old self-source packets
remain privately preserved; they are not product inputs or website downloads.
