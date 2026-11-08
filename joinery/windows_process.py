"""Wait for owned Windows descendants using stable native process handles."""

from __future__ import annotations

import ctypes
import importlib
import time
from ctypes import wintypes


class _ProcessEntry(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


class WindowsProcessTree:
    """Capture handles before taskkill; parent exit alone does not reap children.

    The process snapshot contains only IDs and parent IDs. No command line or
    account configuration is collected. Handles prevent PID reuse from turning
    a wait into an operation on an unrelated process.
    """

    def __init__(self, root: int) -> None:
        self.native = importlib.import_module("ctypes")
        self.kernel = self.native.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        self.kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        self.kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry)]
        self.kernel.Process32FirstW.restype = wintypes.BOOL
        self.kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry)]
        self.kernel.Process32NextW.restype = wintypes.BOOL
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.kernel.WaitForSingleObject.restype = wintypes.DWORD
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.CloseHandle.restype = wintypes.BOOL
        self.handles: list[int] = []
        snapshot = self.kernel.CreateToolhelp32Snapshot(0x00000002, 0)
        if snapshot == ctypes.c_void_p(-1).value:
            raise self.native.WinError(self.native.get_last_error())
        try:
            entry = _ProcessEntry()
            entry.dwSize = ctypes.sizeof(entry)
            parents: dict[int, int] = {}
            present = self.kernel.Process32FirstW(snapshot, ctypes.byref(entry))
            while present:
                parents[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
                present = self.kernel.Process32NextW(snapshot, ctypes.byref(entry))
            if self.native.get_last_error() != 18:  # ERROR_NO_MORE_FILES
                raise self.native.WinError(self.native.get_last_error())
            owned = {root}
            while descendants := {
                pid for pid, parent in parents.items() if parent in owned
            } - owned:
                owned.update(descendants)
            for pid in owned:
                handle = self.kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
                if handle:
                    self.handles.append(int(handle))
                elif self.native.get_last_error() != 87:  # Already exited / invalid PID
                    raise self.native.WinError(self.native.get_last_error())
        except BaseException:
            self.close()
            raise
        finally:
            self.kernel.CloseHandle(snapshot)

    def wait(self, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        for handle in self.handles:
            milliseconds = max(0, int((deadline - time.monotonic()) * 1000))
            result = self.kernel.WaitForSingleObject(handle, milliseconds)
            if result == 0x00000102:  # WAIT_TIMEOUT
                raise TimeoutError("A cancelled provider descendant has not exited.")
            if result != 0:
                raise self.native.WinError(self.native.get_last_error())

    def close(self) -> None:
        for handle in self.handles:
            self.kernel.CloseHandle(handle)
        self.handles.clear()

    def wait_and_close(self, timeout: float) -> None:
        """The waiting worker owns handle cleanup even if its caller is cancelled."""
        try:
            self.wait(timeout)
        finally:
            self.close()
