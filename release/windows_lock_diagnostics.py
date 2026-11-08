"""Bounded, scoped Windows lock diagnostics; never stop/restart an application.

Restart Manager cannot identify directory locks (RmGetList returns access denied
for a registered directory). Query only remaining regular files and report that
limitation explicitly. Process metadata contains PIDs/status, never arguments,
friendly window names, credential contents, or provider output.

https://learn.microsoft.com/en-us/windows/win32/api/restartmanager/nf-restartmanager-rmgetlist
"""

from __future__ import annotations

import ctypes
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


def _resource_files(resource: Path) -> list[Path]:
    """Bound metadata enumeration and never follow symlinks or Windows junctions."""
    if resource.is_symlink() or resource.is_junction():
        return []
    if resource.is_file():
        return [resource.absolute()]
    result = []
    for index, (folder, directories, names) in enumerate(
        os.walk(resource, followlinks=False)
    ):
        if index >= 64:
            break
        directories[:] = [
            name
            for name in directories
            if not (Path(folder) / name).is_symlink()
            and not (Path(folder) / name).is_junction()
        ]
        for name in sorted(names):
            path = Path(folder) / name
            if path.is_file() and not path.is_symlink():
                result.append(path.absolute())
                if len(result) >= 32:
                    return result
    return result


def diagnose_locks(resource: Path, owned_process_ids: list[int]) -> dict[str, Any]:
    """Return diagnostics within five seconds, preserving the caller's failure."""
    if sys.platform != "win32":
        return {"supported": False, "platform": sys.platform}
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "release.windows_lock_diagnostics",
                str(resource),
                *(str(pid) for pid in owned_process_ids if pid > 0),
            ],
            cwd=Path(__file__).resolve().parents[1],
            env={
                **{
                    key: value
                    for key, value in os.environ.items()
                    if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR"}
                },
                "PYTHONUTF8": "1",
            },
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=5,
        )
        if result.returncode:
            return {"supported": True, "diagnostic_process_exit": result.returncode}
        report = json.loads(result.stdout)
        return (
            report
            if isinstance(report, dict)
            else {"diagnostic_error": "invalid_result"}
        )
    except subprocess.TimeoutExpired:
        return {"supported": True, "diagnostic_error": "timeout"}
    except (OSError, ValueError) as exc:
        return {"supported": True, "diagnostic_error": type(exc).__name__}


class _FileTime(ctypes.Structure):
    _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]


class _UniqueProcess(ctypes.Structure):
    _fields_ = [("pid", ctypes.c_uint32), ("start", _FileTime)]


class _ProcessInfo(ctypes.Structure):
    _fields_ = [
        ("process", _UniqueProcess),
        ("application_name", ctypes.c_wchar * 256),
        ("service_name", ctypes.c_wchar * 64),
        ("application_type", ctypes.c_int32),
        ("application_status", ctypes.c_uint32),
        ("session_id", ctypes.c_uint32),
        ("restartable", ctypes.c_int32),
    ]


def _native_report(resource: Path, owned: list[int]) -> dict[str, Any]:
    native = importlib.import_module("ctypes")
    kernel = native.WinDLL("kernel32.dll", use_last_error=True, winmode=0x800)
    manager = native.WinDLL("rstrtmgr.dll", use_last_error=True, winmode=0x800)
    kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int32, ctypes.c_uint32]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel.WaitForSingleObject.restype = ctypes.c_uint32
    kernel.GetProcessTimes.argtypes = [
        ctypes.c_void_p,
        *([ctypes.POINTER(_FileTime)] * 4),
    ]
    kernel.GetProcessTimes.restype = ctypes.c_int32
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.restype = ctypes.c_int32
    manager.RmStartSession.argtypes = [
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.c_uint32,
        ctypes.c_wchar_p,
    ]
    manager.RmStartSession.restype = ctypes.c_uint32
    manager.RmRegisterResources.argtypes = [
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_wchar_p),
        ctypes.c_uint32,
        ctypes.POINTER(_UniqueProcess),
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_wchar_p),
    ]
    manager.RmRegisterResources.restype = ctypes.c_uint32
    manager.RmGetList.argtypes = [
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.POINTER(_ProcessInfo),
        ctypes.POINTER(ctypes.c_uint32),
    ]
    manager.RmGetList.restype = ctypes.c_uint32
    manager.RmEndSession.argtypes = [ctypes.c_uint32]
    manager.RmEndSession.restype = ctypes.c_uint32

    processes = []
    registrations = []
    for pid in sorted(set(owned)):
        if pid <= 0:
            continue
        handle = kernel.OpenProcess(0x1000 | 0x100000, False, pid)
        if not handle:
            processes.append(
                {"pid": pid, "open_process_error": native.get_last_error()}
            )
            continue
        try:
            state = int(kernel.WaitForSingleObject(handle, 0))
            processes.append(
                {"pid": pid, "running": state == 0x102, "wait_result": state}
            )
            if state == 0x102:
                start, end, system, user = (_FileTime() for _ in range(4))
                if kernel.GetProcessTimes(
                    handle,
                    ctypes.byref(start),
                    ctypes.byref(end),
                    ctypes.byref(system),
                    ctypes.byref(user),
                ):
                    registrations.append(_UniqueProcess(pid, start))
        finally:
            kernel.CloseHandle(handle)

    def query(files: list[Path], applications: list[_UniqueProcess]) -> dict[str, Any]:
        if not files and not applications:
            return {"status": "no_resources", "owners": []}
        session = ctypes.c_uint32()
        key = ctypes.create_unicode_buffer(33)
        error = int(manager.RmStartSession(ctypes.byref(session), 0, key))
        if error:
            return {"stage": "start_session", "winerror": error}
        try:
            names = (ctypes.c_wchar_p * len(files))(*(str(path) for path in files))
            apps = (_UniqueProcess * len(applications))(*applications)
            error = int(
                manager.RmRegisterResources(
                    session,
                    len(files),
                    names if files else None,
                    len(applications),
                    apps if applications else None,
                    0,
                    None,
                )
            )
            if error:
                return {"stage": "register", "winerror": error}
            capacity = 0
            for _ in range(3):
                needed, count, reboot = (
                    ctypes.c_uint32(),
                    ctypes.c_uint32(capacity),
                    ctypes.c_uint32(),
                )
                info = (_ProcessInfo * capacity)()
                error = int(
                    manager.RmGetList(
                        session,
                        ctypes.byref(needed),
                        ctypes.byref(count),
                        info if capacity else None,
                        ctypes.byref(reboot),
                    )
                )
                if error == 234 and 0 < needed.value <= 256:
                    capacity = needed.value
                    continue
                if error:
                    return {"stage": "get_list", "winerror": error}
                return {
                    "status": "ok",
                    "owners": [
                        {
                            "pid": item.process.pid,
                            "application_type": item.application_type,
                            "session_id": item.session_id,
                        }
                        for item in info[: count.value]
                    ],
                }
            return {"stage": "get_list", "winerror": 234}
        finally:
            manager.RmEndSession(session)

    files = _resource_files(resource)
    return {
        "supported": True,
        "directory_owner_detection": "unsupported_by_restart_manager",
        "file_count": len(files),
        "file_lock_owners": query(files, []),
        "owned_processes": processes,
        "registered_process_dependencies": query([], registrations),
    }


if __name__ == "__main__":
    try:
        print(
            json.dumps(
                _native_report(Path(sys.argv[1]), [int(pid) for pid in sys.argv[2:]])
            )
        )
    except (OSError, ValueError, TypeError, AttributeError) as error:
        print(json.dumps({"supported": True, "diagnostic_error": type(error).__name__}))
