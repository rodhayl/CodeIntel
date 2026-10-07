"""Source descriptors stay owned until binary handle construction succeeds."""
import os

import pytest

from codeintel.freshness.production import _open_canonical_repo_file
from codeintel.freshness.snapshot_binding import _open_repo_file


@pytest.mark.skipif(os.name != 'posix', reason='POSIX descriptor ownership')
@pytest.mark.parametrize('opener', [_open_canonical_repo_file, _open_repo_file])
@pytest.mark.parametrize('failure', [KeyboardInterrupt, MemoryError, OSError])
def test_failed_handle_construction_closes_source_descriptor(tmp_path, monkeypatch, opener, failure):
    (tmp_path / 'nested').mkdir()
    (tmp_path / 'nested/source.py').write_text('value = 1\n')
    captured = []
    error = failure('injected handle-construction failure')
    def fail(fd, mode):
        captured.append(fd)
        raise error
    monkeypatch.setattr(os, 'fdopen', fail)
    try:
        with pytest.raises(failure) as caught:
            opener(str(tmp_path), 'nested/source.py')
        assert caught.value is error
        assert len(captured) == 1
        with pytest.raises(OSError):
            os.fstat(captured[0])
    finally:
        # Keep the regression safe when run against the old leaking version.
        for fd in captured:
            try:
                os.close(fd)
            except OSError:
                pass


@pytest.mark.parametrize('opener', [_open_canonical_repo_file, _open_repo_file])
def test_source_handle_transfer_keeps_normal_reading(tmp_path, opener):
    raw = 'value = "雪山"\n'.encode()
    (tmp_path / 'source.py').write_bytes(raw)
    with opener(str(tmp_path), 'source.py') as stream:
        fd = stream.fileno()
        assert stream.read() == raw
    with pytest.raises(OSError):
        os.fstat(fd)
