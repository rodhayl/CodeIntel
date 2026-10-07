"""Release interruptions cannot forget live descriptors or pin later rebuilds."""
import asyncio
import os
import threading
from types import SimpleNamespace

import pytest

from codeintel.compat import fcntl
from codeintel.freshness.barrier import GenerationBarrier
from codeintel.freshness.hardened import HardenedProductionGenerationBarrier


pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX file-lock ownership")


def _bare_barrier():
    barrier = object.__new__(HardenedProductionGenerationBarrier)
    barrier._lock = threading.RLock()
    barrier._rebuild_lock_file = None
    barrier._active_lease_fd = None
    barrier._leased_generation_id = None
    barrier._retained_transition_lease_fds = []
    barrier.gen_store = SimpleNamespace(close=lambda: None)
    return barrier


@pytest.mark.parametrize("error_type", [OSError, KeyboardInterrupt, asyncio.CancelledError])
def test_rebuild_unlock_failure_still_closes_fd_and_unblocks_next_owner(tmp_path, monkeypatch, error_type):
    path = tmp_path / "rebuild.lock"
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    barrier = _bare_barrier()
    barrier._rebuild_lock_file = fd
    failure = error_type("interrupted rebuild unlock")
    real_flock = fcntl.flock

    def fail_unlock(target, operation):
        if target == fd and operation == fcntl.LOCK_UN:
            raise failure
        return real_flock(target, operation)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(fcntl, "flock", fail_unlock)
            with pytest.raises(error_type) as caught:
                barrier.release_rebuild_lock()
            assert caught.value is failure
        assert barrier._rebuild_lock_file is None
        with pytest.raises(OSError):
            os.fstat(fd)
        contender = os.open(path, os.O_RDWR)
        try:
            real_flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(contender)
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


@pytest.mark.parametrize("error_type", [KeyboardInterrupt, asyncio.CancelledError])
def test_retained_lease_cleanup_continues_after_unlock_cancellation(tmp_path, monkeypatch, error_type):
    barrier = _bare_barrier()
    descriptors = [os.open(tmp_path / str(i), os.O_CREAT | os.O_RDWR, 0o600) for i in range(2)]
    barrier._retained_transition_lease_fds = descriptors.copy()
    failure = error_type("interrupted retained lease unlock")
    real_flock = fcntl.flock

    def fail_first_unlock(fd, operation):
        if fd == descriptors[0]:
            raise failure
        return real_flock(fd, operation)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(fcntl, "flock", fail_first_unlock)
            with pytest.raises(error_type) as caught:
                barrier._release_retained_transition_leases()
            assert caught.value is failure
        assert barrier._retained_transition_lease_fds == []
        for fd in descriptors:
            with pytest.raises(OSError):
                os.fstat(fd)
    finally:
        for fd in descriptors:
            try:
                os.close(fd)
            except OSError:
                pass


@pytest.mark.parametrize("error_type", [KeyboardInterrupt, asyncio.CancelledError])
def test_close_preserves_cancellation_after_attempting_other_owned_cleanup(monkeypatch, error_type):
    barrier = _bare_barrier()
    events = []
    failure = error_type("interrupted close")

    def fail_rebuild_release():
        events.append("rebuild")
        raise failure

    with monkeypatch.context() as patch:
        patch.setattr(barrier, "release_rebuild_lock", fail_rebuild_release)
        patch.setattr(barrier, "_switch_generation_lease", lambda generation: events.append("lease"))
        barrier.gen_store = SimpleNamespace(close=lambda: events.append("storage"))
        with pytest.raises(error_type) as caught:
            barrier.close()
        assert caught.value is failure
        assert events == ["rebuild", "lease"]  # The service owns storage closure.


@pytest.mark.parametrize("error_type", [KeyboardInterrupt, asyncio.CancelledError])
def test_gc_unlock_cancellation_closes_the_collected_generation_lease(tmp_path, monkeypatch, error_type):
    barrier = _bare_barrier()
    barrier.repo_id = "repo"
    active = SimpleNamespace(generation_id="gen_new", metadata={})
    retired = SimpleNamespace(generation_id="gen_old", metadata={})
    barrier.gen_store = SimpleNamespace(
        list_generations=lambda repo: [active, retired],
        get_active_generation=lambda repo: active,
        delete_generation_data=lambda generation: None,
        close=lambda: None,
    )
    fd = os.open(tmp_path / "lease", os.O_CREAT | os.O_RDWR, 0o600)
    failure = error_type("interrupted GC unlock")
    real_flock = fcntl.flock

    def fail_unlock(target, operation):
        if target == fd and operation == fcntl.LOCK_UN:
            raise failure
        return real_flock(target, operation)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(barrier, "_open_generation_lease_fd", lambda generation: fd)
            patch.setattr(barrier, "_remove_generation_sidecar", lambda generation: None)
            patch.setattr(fcntl, "flock", fail_unlock)
            with pytest.raises(error_type) as caught:
                barrier._gc_generations(retain=1)
            assert caught.value is failure
        with pytest.raises(OSError):
            os.fstat(fd)
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def test_hardened_close_keeps_first_cancellation_when_retained_cleanup_also_fails(monkeypatch):
    barrier = _bare_barrier()
    primary = KeyboardInterrupt("primary rebuild cancellation")
    secondary = asyncio.CancelledError("secondary retained cancellation")
    with monkeypatch.context() as patch:
        patch.setattr(barrier, "release_rebuild_lock", lambda: (_ for _ in ()).throw(primary))
        patch.setattr(barrier, "_release_retained_transition_leases", lambda: (_ for _ in ()).throw(secondary))
        with pytest.raises(KeyboardInterrupt) as caught:
            barrier.close()
        assert caught.value is primary
        assert any("secondary retained cancellation" in note for note in primary.__notes__)


@pytest.mark.parametrize("error_type", [KeyboardInterrupt, asyncio.CancelledError])
def test_real_production_service_closes_active_lease_after_rebuild_release_cancel(tmp_path, monkeypatch, error_type):
    from codeintel.service.production import create_default_service

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("def target(): return 1\n")
    service = create_default_service(str(repo), state_dir=str(tmp_path / "state"))
    service.reindex()
    service.barrier.acquire_rebuild_lock()
    rebuild_fd = service.barrier._rebuild_lock_file
    active_fd = service.barrier._active_lease_fd
    failure = error_type("real rebuild cancellation")
    real_flock = fcntl.flock

    def fail_rebuild_unlock(fd, operation):
        if fd == rebuild_fd and operation == fcntl.LOCK_UN:
            raise failure
        return real_flock(fd, operation)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(fcntl, "flock", fail_rebuild_unlock)
            with pytest.raises(error_type) as caught:
                service.close()
            assert caught.value is failure
        for fd in (rebuild_fd, active_fd):
            with pytest.raises(OSError):
                os.fstat(fd)
        assert service.generation_store._closed
    finally:
        service.close()
