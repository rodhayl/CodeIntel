"""Cross-platform fcntl compatibility shim for Windows."""

from __future__ import annotations

import os
import sys

if sys.platform != "win32":
    import fcntl as _native_fcntl
    from fcntl import *  # noqa: F401, F403
    flock = _native_fcntl.flock
else:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    LOCK_SH = 1
    LOCK_EX = 2
    LOCK_NB = 4
    LOCK_UN = 8

    F_GETFD = 1
    F_SETFD = 2
    FD_CLOEXEC = 1

    LOCKFILE_FAIL_IMMEDIATELY = 1
    LOCKFILE_EXCLUSIVE_LOCK = 2
    ERROR_LOCK_VIOLATION = 33
    ERROR_SHARING_VIOLATION = 32
    ERROR_NOT_LOCKED = 158

    ULONG_PTR = ctypes.c_size_t

    class _OVERLAPPED(ctypes.Structure):
        _fields_ = [
            ("Internal", ULONG_PTR),
            ("InternalHigh", ULONG_PTR),
            ("Offset", wintypes.DWORD),
            ("OffsetHigh", wintypes.DWORD),
            ("hEvent", wintypes.HANDLE),
        ]

    assert ctypes.sizeof(_OVERLAPPED) == (32 if ctypes.sizeof(ctypes.c_void_p) == 8 else 20)

    _kernel32 = ctypes.windll.kernel32

    _LockFileEx = _kernel32.LockFileEx
    _LockFileEx.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(_OVERLAPPED),
    ]
    _LockFileEx.restype = wintypes.BOOL

    _UnlockFileEx = _kernel32.UnlockFileEx
    _UnlockFileEx.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(_OVERLAPPED),
    ]
    _UnlockFileEx.restype = wintypes.BOOL

    def flock(fd: int, operation: int) -> None:
        """Portable flock implementation using Win32 LockFileEx/UnlockFileEx."""
        if isinstance(fd, os.PathLike) or hasattr(fd, "fileno"):
            fd = fd.fileno()
        try:
            handle = msvcrt.get_osfhandle(fd)
        except OSError:
            raise

        ov = _OVERLAPPED()
        nbytes = 0x7FFFFFFF

        if operation & LOCK_UN:
            res = _UnlockFileEx(handle, 0, nbytes, 0, ctypes.byref(ov))
            if not res:
                err = ctypes.GetLastError()
                if err != ERROR_NOT_LOCKED:
                    raise OSError(err, ctypes.FormatError(err))
            return

        flags = 0
        if operation & LOCK_NB:
            flags |= LOCKFILE_FAIL_IMMEDIATELY
        if operation & LOCK_EX:
            flags |= LOCKFILE_EXCLUSIVE_LOCK

        # LockFileEx(hFile, dwFlags, dwReserved, nNumberOfBytesToLockLow, nNumberOfBytesToLockHigh, lpOverlapped)
        res = _LockFileEx(handle, flags, 0, nbytes, 0, ctypes.byref(ov))
        if not res:
            err = ctypes.GetLastError()
            if err in (ERROR_LOCK_VIOLATION, ERROR_SHARING_VIOLATION):
                raise BlockingIOError(
                    err, f"Resource temporarily unavailable: {ctypes.FormatError(err)}"
                )
            raise OSError(err, ctypes.FormatError(err))

    def fcntl(fd: int, op: int, arg: int = 0) -> int:
        """Minimal fcntl stub for FD_CLOEXEC."""
        return 0

    __all__ = [
        "LOCK_SH",
        "LOCK_EX",
        "LOCK_NB",
        "LOCK_UN",
        "F_GETFD",
        "F_SETFD",
        "FD_CLOEXEC",
        "flock",
        "fcntl",
    ]
