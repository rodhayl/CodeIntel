"""Canonical output is unchanged when extractors stop manufacturing raw chunks."""
from dataclasses import asdict
import hashlib
import json

import pytest

from codeintel.parsing.treesitter_parser import TreeSitterCodeParser
import codeintel.parsing.treesitter_parser as parser_module


# Frozen immediately before removing discarded chunk construction, after the
# lambda/arrow syntax fixes. Includes IDs, spans, owners, relations and text.
CASES = [('accessor.js',
  '// π\r\n'
  'class Client { get value() { return read(); } set value(v) { write(v); } }\r\n'
  'function outer() { function inner() { return 1; } return inner(); }\r\n'
  'const run = () => target();\r\n',
  '2d0ba0e2812dd522724fd05fc71c2e2044f39a243bbdcdf4e113405719c1ec54'),
 ('element.tsx',
  'const render = () => <div>λ</div>;\r\n',
  '12c6708265347d65e050e2e4801ed197cb65e74dafa6ea2b45f838ef16b2fa83'),
 ('empty.js', '', 'f60a120875f4fcfda9084fc91217919aa20b16c106448cc874e0b321871561ee'),
 ('empty.py', '', '0e1ee50956174852de4f6b296551528486e3d18d520e4ed0bad2433b96b452d7'),
 ('empty.ts', '', 'b38d744c3daa9f27f6a535c5c61047e5b921ae6193aa81f97cb89714623fd025'),
 ('empty.txt', '', '8e152e016153c8f3c0a02a345ff8d2c053de89140be51f601832f95d2afb3ff1'),
 ('fallback.txt', 'π\r\né\n尾', '1272ceb8974a2b7acf801091f6f940dea5ca320798b8db6dbe005c8d3e076c38'),
 ('nested.py',
  '# π\r\n'
  'class Client:\r\n'
  '    @property\r\n'
  "    def value(self): return 'é'\r\n"
  '    @value.setter\r\n'
  '    def value(self, item): self._value = item\r\n'
  'def outer():\r\n'
  "    def inner(): return 'λ'\r\n"
  '    return inner()\r\n',
  '9ec9bcbbf64813799b524a4883582fdfd1ea9da44ba0bfb174ca7623a306384d'),
 ('single.py', 'value = "é"', '2e0f1ac1a238e60fa15363c80ad51016d5bca51468b6d6d60e53ad3d89105fbf'),
 ('spaces.py', ' \r\n\t\n', '8abd4fb3b53154a871a6858041f1328962244d013a6d58eefdfcc1714bbecb75'),
 ('spaces.ts', ' \r\n\t\n', '8abd4fb3b53154a871a6858041f1328962244d013a6d58eefdfcc1714bbecb75'),
 ('typed.ts',
  'interface Client { go(): number; }\r\n'
  "function target() { return 'é'; }\r\n"
  'const run: () => string = () => target();\r\n',
  'd79ca37151ba113a9c344f965b5d58ffaa41ea7207c848f840a2ea369ef17833')]


@pytest.mark.parametrize(("path", "source", "expected"), CASES)
def test_raw_chunk_removal_preserves_complete_parser_output(path, source, expected):
    entities, relations, chunks = TreeSitterCodeParser().parse_file("repo", "file", "gen", path, source)
    payload = {"entities": [asdict(item) for item in entities],
               "relations": [asdict(item) for item in relations],
               "chunks": [asdict(item) for item in chunks]}
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()
    assert hashlib.sha256(encoded).hexdigest() == expected


@pytest.mark.parametrize("path", ["sample.py", "sample.js", "sample.ts"])
def test_supported_extractors_do_not_construct_discarded_chunks(monkeypatch, path):
    def reject_raw_chunk(*args, **kwargs):
        raise AssertionError("supported extractor constructed a raw chunk")

    monkeypatch.setattr(parser_module, "Chunk", reject_raw_chunk)
    source = "def run(): return 1\n" if path.endswith(".py") else "function run() { return 1; }\n"
    entities, _, chunks = TreeSitterCodeParser().parse_file("repo", "file", "gen", path, source)
    assert entities and chunks
