"""One parser read performs one hash lookup, not a defensive whole-map copy."""
import hashlib

import pytest

from codeintel.core.security import FreshnessBusyError
from codeintel.freshness import snapshot_binding as module


def test_parser_read_does_not_use_full_snapshot_inspection(tmp_path, monkeypatch):
    raw = b'value = 1\n'
    (tmp_path / 'a.py').write_bytes(raw)
    hashes = {f'other/{i}.py': 'a' * 64 for i in range(1000)}
    hashes['a.py'] = hashlib.sha256(raw).hexdigest()
    owner = object()
    module.register_generation_snapshot('gen_lookup', hashes, repo_root=str(tmp_path), owner=owner)
    try:
        def forbidden(*args, **kwargs):
            raise AssertionError('parser copied the whole registered snapshot')
        monkeypatch.setattr(module, 'generation_snapshot_hashes', forbidden)
        assert module.read_generation_source_bytes(str(tmp_path), 'a.py', 'gen_lookup') == raw
        with pytest.raises(FreshnessBusyError, match='absent from'):
            module.read_generation_source_bytes(str(tmp_path), 'missing.py', 'gen_lookup')
        (tmp_path / 'a.py').write_bytes(b'value = 2\n')
        with pytest.raises(FreshnessBusyError, match='no longer matches'):
            module.read_generation_source_bytes(str(tmp_path), 'a.py', 'gen_lookup')
    finally:
        module.clear_generation_snapshot('gen_lookup', repo_root=str(tmp_path), owner=owner)


def test_lookup_uses_one_mapping_get_and_inspection_is_defensive(tmp_path):
    raw = b'value = 1\n'
    (tmp_path / 'a.py').write_bytes(raw)
    owner = object()
    hashes = {'a.py': hashlib.sha256(raw).hexdigest()}
    generation = 'gen_instrumented_lookup'
    module.register_generation_snapshot(generation, hashes, repo_root=str(tmp_path), owner=owner)
    class LookupOnly(dict):
        lookups = 0
        def get(self, key, default=None):
            self.lookups += 1
            return super().get(key, default)
        def keys(self):
            raise AssertionError('whole-map copy attempted')
        def __iter__(self):
            raise AssertionError('whole-map iteration attempted')
    key = module._registry_key(generation, str(tmp_path))
    try:
        snapshot = module.generation_snapshot_hashes(generation, repo_root=str(tmp_path))
        snapshot['a.py'] = '0' * 64
        assert module.generation_snapshot_hashes(generation, repo_root=str(tmp_path)) == hashes
        registered = LookupOnly(hashes)
        with module._SNAPSHOT_LOCK:
            module._SNAPSHOTS[key].hashes = registered
        assert module.read_generation_source_bytes(str(tmp_path), 'a.py', generation) == raw
        assert registered.lookups == 1
    finally:
        module.clear_generation_snapshot(generation, repo_root=str(tmp_path), owner=owner)
