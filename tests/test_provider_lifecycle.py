"""Exercise native Windows cancellation of an owned provider subprocess tree."""

import asyncio
import ctypes
import json
import os
import sys
from ctypes import wintypes

import pytest

from bridge.provider_process import stop_process


@pytest.mark.skipif(os.name != "nt", reason="native Windows process-tree lifecycle")
def test_windows_provider_cancel_reaps_owned_descendant() -> None:
    # Hold a real process handle before cancelling: unlike checking a PID, it
    # cannot mistake a reused ID for the original descendant still being alive.
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.TerminateProcess.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL

    async def run() -> None:
        # The executable waits until its descendant is running, then exposes its
        # PID over the provider's normal stdout pipe. No shell or CLI auth is used.
        child_code = "import time; print('ready', flush=True); time.sleep(60)"
        parent_code = (
            "import json,subprocess,sys; "
            "child=subprocess.Popen([sys.executable,'-u','-c',"
            + repr(child_code)
            + "],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL); "
            "assert child.stdout.readline().strip()==b'ready'; "
            "print(json.dumps({'child':child.pid}),flush=True); sys.stdin.readline()"
        )
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-u",
            "-c",
            parent_code,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        handle = None
        try:
            assert process.stdout is not None
            ready = await asyncio.wait_for(process.stdout.readline(), 10)
            descendant = int(json.loads(ready)["child"])
            handle = kernel.OpenProcess(0x100000 | 0x0001, False, descendant)
            assert handle, "The owned provider descendant must be running"
            assert kernel.WaitForSingleObject(handle, 0) == 0x102  # WAIT_TIMEOUT
            await asyncio.wait_for(stop_process(process), 15)
            assert process.returncode is not None
            # taskkill /T must stop descendants too, rather than just allowing
            # the parent pipe to close and reporting apparent success.
            result = kernel.WaitForSingleObject(handle, 0)
            assert result == 0, "Cancelled provider left a running descendant"
            await stop_process(process)  # Closing an already-exited child is safe.
        finally:
            if handle:
                if kernel.WaitForSingleObject(handle, 0) == 0x102:
                    kernel.TerminateProcess(handle, 1)
                    kernel.WaitForSingleObject(handle, 5000)
                kernel.CloseHandle(handle)
            if process.returncode is None:
                process.kill()
                await process.wait()

    asyncio.run(asyncio.wait_for(run(), 35))
